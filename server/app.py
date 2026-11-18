"""
대화 API 서버

실행 (저장소 루트에서):
    pip install -r server/requirements.txt
    cp server/.env.example server/.env   # ANTHROPIC_API_KEY 입력
    python -m rag.build_index            # 최초 1회, 벡터 DB 생성
    uvicorn server.app:app --reload --port 8000

API:
    POST /api/session            → 새 세션 + 첫 질문
    POST /api/chat               → 사용자 답변을 보내고 다음 질문을 받음
                                   (stop=true 또는 "그만" 등의 말이면 질문 종료)
"""

import uuid
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parent / ".env")

from rag.retrieve import search_similar  # noqa: E402

from .llm import LLMError, complete_json  # noqa: E402
from .question_loop import QuestionLoop  # noqa: E402
from .state import SessionState  # noqa: E402

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"],
                   allow_methods=["*"], allow_headers=["*"])

loop = QuestionLoop(complete_json=complete_json,
                    search=lambda q, exclude, k: search_similar(q, exclude=exclude, k=k))

# 세션은 서버 메모리에만 있다 (재시작하면 사라짐). 실제 서비스에서는 DB/Redis로 옮길 것.
SESSIONS: dict[str, SessionState] = {}


class ChatRequest(BaseModel):
    session_id: str
    message: str = ""
    stop: bool = False


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    finished: bool
    state: dict


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/session", response_model=ChatResponse)
def new_session() -> ChatResponse:
    state, reply = loop.start()
    session_id = uuid.uuid4().hex
    SESSIONS[session_id] = state
    return ChatResponse(session_id=session_id, reply=reply, finished=False, state=state.public_view())


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    state = SESSIONS.get(req.session_id)
    if state is None:
        raise HTTPException(404, "세션이 없습니다. /api/session으로 새로 시작하세요.")
    working = state.model_copy(deep=True)      # 중간에 실패하면 상태를 되돌리기 위해 사본에서 작업
    try:
        reply = loop.handle_user_message(working, req.message, stop=req.stop)
    except (LLMError, anthropic.APIError) as e:
        raise HTTPException(502, f"LLM 호출 실패: {e}") from e
    except FileNotFoundError as e:             # 벡터 DB가 아직 없음
        raise HTTPException(503, str(e)) from e
    SESSIONS[req.session_id] = working
    return ChatResponse(session_id=req.session_id, reply=reply, finished=working.finished,
                        state=working.public_view())
