"""질문 루프 단위 테스트 (가짜 LLM·가짜 검색, API 키 불필요). 실행: python -m pytest tests"""

import numpy as np
import pytest

from rag.vector_db import VectorDB
from server.question_loop import CLOSING_MESSAGE, MAX_FOLLOWUPS, QuestionLoop


def make_docs(n, prefix="doc"):
    return [{"doc_id": f"{prefix}_{i}", "title": f"t{i}", "url": f"u{i}", "body": f"본문 {i}"} for i in range(n)]


class FakeLLM:
    """step 이름별로 미리 정한 응답을 차례로 돌려준다."""

    def __init__(self, tails=None, followups=None):
        self.calls = []
        self.tails = list(tails or [])
        self.followups = list(followups or [])

    def __call__(self, step, system, user, schema):
        self.calls.append((step, system, user))
        if step == "step1_query":
            return {"query": "성수동 카페 방문 후기"}
        if step == "step3_aspects":
            ids = [line.split('"')[1] for line in user.splitlines() if line.startswith("<doc id=")]
            return {"aspects": [
                {"name": "분위기", "elements": [{"name": "조명", "doc_ids": ids[:2] + ["doc_없음"]},
                                               {"name": "음악", "doc_ids": ids[2:3]}]},
                {"name": "가격", "elements": [{"name": "메뉴별 가격대", "doc_ids": ids[3:4]}]},
            ]}
        if step == "step4_tail_question":
            q = self.tails.pop(0) if self.tails else None
            return {"question": q, "target": "분위기/조명", "reason": "r"}
        if step == "step5_followup":
            q = self.followups.pop(0) if self.followups else None
            return {"need_followup": q is not None, "question": q, "reason": "r"}
        raise AssertionError(step)

    def steps(self):
        return [c[0] for c in self.calls]


class FakeSearch:
    def __init__(self, docs):
        self.docs = docs
        self.excludes = []

    def __call__(self, query, exclude, k):
        exclude = set(exclude)
        self.excludes.append(exclude)
        return [d for d in self.docs if d["doc_id"] not in exclude][:k]


def test_first_answer_runs_round_and_asks_tail_question():
    llm, search = FakeLLM(tails=["조명은 어떤 느낌이었나요?"]), FakeSearch(make_docs(15))
    loop = QuestionLoop(llm, search)
    s, opening = loop.start()
    reply = loop.handle_user_message(s, "성수동 카페에 다녀왔어요")

    assert reply == "조명은 어떤 느낌이었나요?"
    assert llm.steps() == ["step1_query", "step3_aspects", "step4_tail_question"]
    assert len(s.cached_docs) == 10 and len(s.used_doc_ids) == 10 and s.round == 1
    assert s.aspect_map["분위기"]["조명"] == ["doc_0", "doc_1"]          # 없는 doc_id는 버림
    assert s.asked_questions[-1].kind == "tail"
    assert [m.role for m in s.conversation] == ["assistant", "user", "assistant"]
    # Step 4에는 원문을 넣지 않는다
    assert "본문 0" not in llm.calls[-1][2]


def test_followups_capped_then_next_tail():
    llm = FakeLLM(tails=["Q1", "Q2"], followups=["F1", "F2", "F3"])
    loop = QuestionLoop(llm, FakeSearch(make_docs(15)))
    s, _ = loop.start()
    assert loop.handle_user_message(s, "카페 다녀옴") == "Q1"
    assert loop.handle_user_message(s, "좋았어요") == "F1"
    assert loop.handle_user_message(s, "음") == "F2"
    # 추가질문 2회 소진 → Step 5 없이 다음 꼬리질문
    assert loop.handle_user_message(s, "그냥요") == "Q2"
    assert llm.steps().count("step5_followup") == MAX_FOLLOWUPS
    assert [q.kind for q in s.asked_questions] == ["tail", "followup", "followup", "tail"]
    assert s.followup_count == 0


def test_step5_gets_reference_docs_for_target():
    llm = FakeLLM(tails=["Q1", "Q2"], followups=[None])
    loop = QuestionLoop(llm, FakeSearch(make_docs(15)))
    s, _ = loop.start()
    loop.handle_user_message(s, "카페")
    loop.handle_user_message(s, "답")
    step5_input = [c for c in llm.calls if c[0] == "step5_followup"][0][2]
    assert "본문 0" in step5_input and "본문 1" in step5_input and "본문 5" not in step5_input
    assert [d.doc_id for d in loop.reference_docs(s, "차별점")] == []
    assert {d.doc_id for d in loop.reference_docs(s, "분위기")} == {"doc_0", "doc_1", "doc_2"}


def test_null_tail_starts_new_round_excluding_used_docs():
    llm = FakeLLM(tails=[None, "새 라운드 질문"])
    search = FakeSearch(make_docs(25))
    loop = QuestionLoop(llm, search)
    s, _ = loop.start()
    assert loop.handle_user_message(s, "카페") == "새 라운드 질문"
    assert s.round == 2
    assert search.excludes[1] == {f"doc_{i}" for i in range(10)}
    assert [d.doc_id for d in s.cached_docs] == [f"doc_{i}" for i in range(10, 20)]
    assert len(s.used_doc_ids) == 20


def test_partial_then_empty_search_finishes():
    llm = FakeLLM(tails=[None, None])
    loop = QuestionLoop(llm, FakeSearch(make_docs(13)))
    s, _ = loop.start()
    assert loop.handle_user_message(s, "카페") == CLOSING_MESSAGE
    assert s.round == 2                            # 10개 → 3개 → 0개
    assert s.finished and s.finish_reason == "no_more_docs"


def test_max_rounds_finishes():
    llm = FakeLLM(tails=[None] * 5)
    loop = QuestionLoop(llm, FakeSearch(make_docs(100)))
    s, _ = loop.start()
    assert loop.handle_user_message(s, "카페") == CLOSING_MESSAGE
    assert s.round == 3 and s.finish_reason == "max_rounds"
    assert llm.steps().count("step1_query") == 3


@pytest.mark.parametrize("msg,stop", [("이제 그만 물어봐도 돼요", False), ("", True)])
def test_user_stop(msg, stop):
    llm = FakeLLM(tails=["Q1"])
    loop = QuestionLoop(llm, FakeSearch(make_docs(15)))
    s, _ = loop.start()
    loop.handle_user_message(s, "카페")
    n = len(llm.calls)
    assert loop.handle_user_message(s, msg, stop=stop) == CLOSING_MESSAGE
    assert s.finish_reason == "user_stop" and len(llm.calls) == n
    assert loop.handle_user_message(s, "더 얘기할래요") == CLOSING_MESSAGE    # 종료 후엔 추가 호출 없음
    assert len(llm.calls) == n


def test_vector_db_search_orders_and_excludes(tmp_path):
    docs = make_docs(3)
    emb = np.array([[1, 0], [0.6, 0.8], [0, 1]], dtype=np.float32)
    VectorDB(docs, emb).save(tmp_path)
    db = VectorDB.load(tmp_path)
    q = np.array([1, 0], dtype=np.float32)
    assert [d["doc_id"] for d in db.search(q, k=2)] == ["doc_0", "doc_1"]
    assert [d["doc_id"] for d in db.search(q, k=5, exclude={"doc_0"})] == ["doc_1", "doc_2"]


def test_split_chunks():
    from rag.embedder import split_chunks
    chunks = split_chunks("가" * 200 + ". " + "나" * 200 + ". 다.", max_chars=300)
    assert len(chunks) == 2 and all(len(c) <= 300 for c in chunks)
