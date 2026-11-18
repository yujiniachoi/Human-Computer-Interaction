"""
세션 동안 유지하는 질문 루프 상태.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AskedQuestion(BaseModel):
    question: str
    target: str                              # "차별점" 또는 "측면/하위요소"
    kind: Literal["tail", "followup"]        # 꼬리질문 / 추가질문


class CachedDoc(BaseModel):
    doc_id: str
    title: str = ""
    url: str = ""
    body: str


class SessionState(BaseModel):
    # ---- 명세의 상태
    conversation: list[Message] = Field(default_factory=list)
    cached_docs: list[CachedDoc] = Field(default_factory=list)        # 현재 라운드 검색 결과
    used_doc_ids: list[str] = Field(default_factory=list)             # 모든 라운드 누적
    aspect_map: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    asked_questions: list[AskedQuestion] = Field(default_factory=list)

    # ---- 루프 진행 상황
    round: int = 0                           # 지금까지 시작한 라운드 수
    round_active: bool = False               # 현재 라운드에서 아직 물을 게 남았는지
    last_query: str = ""
    current_tail: Optional[AskedQuestion] = None  # 지금 다루는 꼬리질문
    followup_count: int = 0                  # current_tail에 대해 한 추가질문 수
    finished: bool = False
    finish_reason: Optional[Literal["no_more_docs", "max_rounds", "user_stop"]] = None

    def public_view(self) -> dict:
        """front에 돌려줄 요약 (글 본문 제외)."""
        return {
            "round": self.round,
            "last_query": self.last_query,
            "aspect_map": self.aspect_map,
            "asked_questions": [q.model_dump() for q in self.asked_questions],
            "references": [{"doc_id": d.doc_id, "title": d.title, "url": d.url} for d in self.cached_docs],
            "finished": self.finished,
            "finish_reason": self.finish_reason,
        }
