"""
Claude 호출 래퍼: 시스템 프롬프트 + 입력 → JSON(dict)

- structured outputs(output_config.format)로 스키마에 맞는 JSON만 받는다.
- 안전 분류기가 거절하면 서버 측 fallback이 다른 모델로 자동 재시도한다.
- 모델/effort는 환경변수 LLM_MODEL, LLM_EFFORT로 바꿀 수 있다.
- API 키는 ANTHROPIC_API_KEY 환경변수(server/.env)에서 읽는다.
"""

from __future__ import annotations

import json
import os

import anthropic

MODEL = os.getenv("LLM_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("LLM_EFFORT", "medium")      # low | medium | high | xhigh | max

_client: anthropic.Anthropic | None = None


class LLMError(RuntimeError):
    pass


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def complete_json(step: str, system: str, user: str, schema: dict) -> dict:
    """step은 로그/테스트용 이름 (예: "step1_query")."""
    resp = _get_client().beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": schema},
        },
    )
    if resp.stop_reason == "refusal":
        raise LLMError(f"{step}: 모델이 응답을 거절했습니다 ({resp.stop_details})")
    if resp.stop_reason == "max_tokens":
        raise LLMError(f"{step}: 응답이 max_tokens에서 잘렸습니다")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if text is None:
        raise LLMError(f"{step}: 텍스트 응답이 없습니다 (request id {resp._request_id})")
    return json.loads(text)
