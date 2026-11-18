# Human-Computer-Interaction
2026-2 HCI course Team project — 대화형 블로그 포스팅 작성 도우미

## 폴더 구조

```
front/      UI (정적 웹앱, `npm run dev`)
server/     대화 API + RAG 질문 루프 (FastAPI, Claude)
rag/        블로그 DB 벡터 검색 (임베딩, 인덱스 생성, 검색)
prompts/    작성 AI 프롬프트, 질문 루프 Step별 프롬프트
db/         블로그 포스팅 DB (crawler/ 수집·전처리, raw/, processed/)
tests/      질문 루프 테스트 (가짜 LLM, API 키 불필요)
docs/       보고서
```

## RAG 질문 루프

```
사용자 답변 ─▶ [Step 1] 대화 요약 → 검색 쿼리                (LLM)
              [Step 2] 쿼리 임베딩 → 비슷한 글 10개, 이미 쓴 글 제외  (rag/)
              [Step 3] 10개 글 → 경험 측면 지도 aspect_map    (LLM)
              [Step 4] 꼬리질문 (차별점 우선, 다음은 안 다룬 측면) (LLM)
                 └ null이면 새 라운드 (Step 1로)
              [Step 5] 답변이 충분히 구체적인가? → 추가질문 (최대 2회) (LLM)
                 └ 충분하면 Step 4로
종료: 새로 가져올 글 없음 / 3라운드 / 사용자가 그만 (stop=true 또는 "그만" 등)
```

- 코드: `server/question_loop.py`, 상태: `server/state.py`
- 프롬프트: `prompts/writing_ai.md` (말투·형식), `prompts/question_loop/step*.md`

## 실행

```sh
pip install -r server/requirements.txt
cp server/.env.example server/.env        # ANTHROPIC_API_KEY 입력
python -m rag.build_index                 # db/processed/clean.json 필요
uvicorn server.app:app --reload --port 8000
python -m pytest tests                    # 테스트
```

API: `POST /api/session` → `{session_id, reply}` / `POST /api/chat {session_id, message, stop}` → `{reply, finished, state}`
