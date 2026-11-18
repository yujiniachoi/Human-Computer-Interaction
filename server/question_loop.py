"""
RAG 기반 질문 루프

라운드: Step 1 쿼리 생성 → Step 2 검색 → Step 3 측면 추출 → Step 4 꼬리질문 ⇄ Step 5 추가질문
Step 4에서 더 물을 게 없으면 새 라운드. 종료: 새 글 없음 / 최대 3라운드 / 사용자 중단.

LLM 호출과 검색 함수는 생성자로 주입한다 (테스트에서 가짜로 교체 가능).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

from .state import AskedQuestion, CachedDoc, Message, SessionState

MAX_ROUNDS = 3
DOCS_PER_ROUND = 10
MAX_FOLLOWUPS = 2

OPENING_MESSAGE = "어떤 경험을 글로 쓰고 싶으세요? 어디에 다녀오셨는지, 어땠는지 편하게 들려주세요."
CLOSING_MESSAGE = "질문은 여기까지예요. 지금까지 들려주신 이야기로 글을 정리해 볼게요."
STOP_PHRASES = ("그만", "이제 됐", "됐어요", "충분해", "글 써줘", "글 써 줘", "글 써주세요", "글로 써줘")

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

CompleteJSON = Callable[[str, str, str, dict], dict]          # (step, system, user, schema) -> dict
Search = Callable[[str, Iterable[str], int], list[dict]]      # (query, exclude, k) -> docs

# ---------------------------------------------------------------- JSON 스키마
_NULLABLE_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}

SCHEMA_QUERY = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}
SCHEMA_ASPECTS = {
    "type": "object",
    "properties": {"aspects": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "elements": {"type": "array", "items": {
                "type": "object",
                "properties": {"name": {"type": "string"},
                               "doc_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["name", "doc_ids"],
                "additionalProperties": False,
            }},
        },
        "required": ["name", "elements"],
        "additionalProperties": False,
    }}},
    "required": ["aspects"],
    "additionalProperties": False,
}
SCHEMA_TAIL = {
    "type": "object",
    "properties": {"question": _NULLABLE_STR, "target": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["question", "target", "reason"],
    "additionalProperties": False,
}
SCHEMA_FOLLOWUP = {
    "type": "object",
    "properties": {"need_followup": {"type": "boolean"}, "question": _NULLABLE_STR, "reason": {"type": "string"}},
    "required": ["need_followup", "question", "reason"],
    "additionalProperties": False,
}


def _load(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").strip()


def _fmt_conversation(s: SessionState) -> str:
    return "\n".join(f"{'사용자' if m.role == 'user' else 'AI'}: {m.content}" for m in s.conversation)


def wants_to_stop(message: str) -> bool:
    return any(p in message.replace(" ", "") for p in (p.replace(" ", "") for p in STOP_PHRASES))


class QuestionLoop:
    def __init__(self, complete_json: CompleteJSON, search: Search):
        self.llm = complete_json
        self.search = search
        self.writing_ai_prompt = _load("writing_ai.md")

    # ================================================================ 공개 API
    def start(self) -> tuple[SessionState, str]:
        s = SessionState()
        s.conversation.append(Message(role="assistant", content=OPENING_MESSAGE))
        return s, OPENING_MESSAGE

    def handle_user_message(self, s: SessionState, message: str, stop: bool = False) -> str:
        """사용자 메시지를 받아 다음 AI 메시지(질문 또는 종료 안내)를 돌려준다. s를 직접 갱신한다."""
        if s.finished:
            return CLOSING_MESSAGE
        message = message.strip()
        if message:
            s.conversation.append(Message(role="user", content=message))
        if stop or wants_to_stop(message):
            return self._finish(s, "user_stop")

        # 꼬리질문/추가질문에 대한 답이면 Step 5
        if s.current_tail is not None and s.followup_count < MAX_FOLLOWUPS:
            q = self.step5_followup(s, message)
            if q:
                s.followup_count += 1
                return self._ask(s, q, s.current_tail.target, "followup")

        return self._next_tail_question(s)

    # ================================================================ 루프 제어
    def _next_tail_question(self, s: SessionState) -> str:
        s.current_tail, s.followup_count = None, 0
        while True:
            if not s.round_active and not self._start_round(s):
                return CLOSING_MESSAGE
            result = self.step4_tail_question(s)
            if result.get("question"):
                q = self._ask(s, result["question"], result.get("target", ""), "tail")
                s.current_tail = s.asked_questions[-1]
                return q
            s.round_active = False          # 이번 라운드에서 더 물을 게 없음 → 새 라운드

    def _start_round(self, s: SessionState) -> bool:
        if s.round >= MAX_ROUNDS:
            self._finish(s, "max_rounds")
            return False
        s.last_query = self.step1_query(s)
        if not self.step2_search(s, s.last_query):
            self._finish(s, "no_more_docs")
            return False
        s.round += 1
        s.aspect_map = self.step3_aspects(s)
        s.round_active = True
        return True

    def _ask(self, s: SessionState, question: str, target: str, kind: str) -> str:
        s.asked_questions.append(AskedQuestion(question=question, target=target, kind=kind))
        s.conversation.append(Message(role="assistant", content=question))
        return question

    def _finish(self, s: SessionState, reason: str) -> str:
        s.finished, s.finish_reason = True, reason
        s.current_tail, s.round_active = None, False
        s.conversation.append(Message(role="assistant", content=CLOSING_MESSAGE))
        return CLOSING_MESSAGE

    # ================================================================ Step 1~5
    def step1_query(self, s: SessionState) -> str:
        user = f"<conversation>\n{_fmt_conversation(s)}\n</conversation>"
        return self.llm("step1_query", _load("question_loop/step1_query.md"), user, SCHEMA_QUERY)["query"].strip()

    def step2_search(self, s: SessionState, query: str) -> bool:
        docs = self.search(query, s.used_doc_ids, DOCS_PER_ROUND)
        docs = [d for d in docs if d["doc_id"] not in set(s.used_doc_ids)][:DOCS_PER_ROUND]
        if not docs:
            return False
        s.cached_docs = [CachedDoc(**{k: d.get(k, "") for k in ("doc_id", "title", "url", "body")}) for d in docs]
        s.used_doc_ids.extend(d.doc_id for d in s.cached_docs)
        return True

    def step3_aspects(self, s: SessionState) -> dict[str, dict[str, list[str]]]:
        docs = "\n\n".join(
            f'<doc id="{d.doc_id}">\n제목: {d.title}\n{d.body}\n</doc>' for d in s.cached_docs)
        result = self.llm("step3_aspects", _load("question_loop/step3_aspects.md"),
                          f"<documents>\n{docs}\n</documents>", SCHEMA_ASPECTS)
        valid_ids = {d.doc_id for d in s.cached_docs}
        aspect_map: dict[str, dict[str, list[str]]] = {}
        for a in result.get("aspects", []):
            elements = aspect_map.setdefault(a["name"].strip(), {})
            for e in a.get("elements", []):
                ids = elements.setdefault(e["name"].strip(), [])
                ids.extend(i for i in e.get("doc_ids", []) if i in valid_ids and i not in ids)
        return aspect_map

    def step4_tail_question(self, s: SessionState) -> dict:
        names_only = {aspect: list(elements) for aspect, elements in s.aspect_map.items()}
        asked = [{"question": q.question, "target": q.target} for q in s.asked_questions]
        user = (
            f"<aspect_map>\n{json.dumps(names_only, ensure_ascii=False, indent=1)}\n</aspect_map>\n\n"
            f"<conversation>\n{_fmt_conversation(s)}\n</conversation>\n\n"
            f"<asked_questions>\n{json.dumps(asked, ensure_ascii=False, indent=1)}\n</asked_questions>"
        )
        system = _load("question_loop/step4_tail_question.md") + "\n\n" + self.writing_ai_prompt
        return self.llm("step4_tail_question", system, user, SCHEMA_TAIL)

    def step5_followup(self, s: SessionState, answer: str) -> str | None:
        tail = s.current_tail
        last_question = s.asked_questions[-1].question if s.asked_questions else tail.question
        refs = self.reference_docs(s, tail.target)
        refs_text = "\n\n".join(f'<doc id="{d.doc_id}">\n제목: {d.title}\n{d.body}\n</doc>' for d in refs) or "(없음)"
        user = (
            f"<tail_question>\n질문: {tail.question}\ntarget: {tail.target}\n</tail_question>\n\n"
            f"<last_question>\n{last_question}\n</last_question>\n\n"
            f"<answer>\n{answer}\n</answer>\n\n"
            f"<conversation>\n{_fmt_conversation(s)}\n</conversation>\n\n"
            f"<references>\n{refs_text}\n</references>"
        )
        system = _load("question_loop/step5_followup.md") + "\n\n" + self.writing_ai_prompt
        result = self.llm("step5_followup", system, user, SCHEMA_FOLLOWUP)
        return result.get("question") if result.get("need_followup") and result.get("question") else None

    @staticmethod
    def reference_docs(s: SessionState, target: str) -> list[CachedDoc]:
        """target("측면/하위요소" 또는 "측면")에 연결된 cached_docs. "차별점"이면 없음."""
        if not target or target.strip() == "차별점":
            return []
        aspect, _, element = (p.strip() for p in target.partition("/"))
        elements = s.aspect_map.get(aspect, {})
        if element:
            ids = elements.get(element, [])
        else:
            ids = [i for doc_ids in elements.values() for i in doc_ids]
        wanted = set(ids)
        return [d for d in s.cached_docs if d.doc_id in wanted]
