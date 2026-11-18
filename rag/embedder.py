"""
텍스트 → 임베딩 벡터 (로컬 sentence-transformers 모델)

- 모델은 환경변수 EMBED_MODEL로 바꿀 수 있다. (기본: 한국어 특화 jhgan/ko-sroberta-multitask)
- 이 모델은 한 번에 짧은 텍스트(약 128토큰)만 보므로, 긴 블로그 글은 문단 단위로 나눠
  각각 임베딩한 뒤 평균을 내서 글 하나의 벡터로 만든다.
- 모든 벡터는 길이 1로 정규화한다 → 내적 = 코사인 유사도.
"""

from __future__ import annotations

import os
import re

import numpy as np

MODEL_NAME = os.getenv("EMBED_MODEL", "jhgan/ko-sroberta-multitask")
CHUNK_CHARS = 300          # 문서를 나눌 때 청크 하나의 최대 글자 수


def split_chunks(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    """문장 경계에서 끊어 max_chars 이하의 청크로 나눈다."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?。])\s+|\n+", text) if s.strip()]
    chunks, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) + 1 > max_chars:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return chunks or [text[:max_chars]]


def _normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


class Embedder:
    def __init__(self, model_name: str = MODEL_NAME):
        from sentence_transformers import SentenceTransformer   # 무거워서 필요할 때만 import
        self.model = SentenceTransformer(model_name)

    def embed_query(self, text: str) -> np.ndarray:
        return self.model.encode([text], normalize_embeddings=True)[0].astype(np.float32)

    def embed_document(self, text: str) -> np.ndarray:
        vecs = self.model.encode(split_chunks(text), normalize_embeddings=True)
        return _normalize(vecs.mean(axis=0)).astype(np.float32)
