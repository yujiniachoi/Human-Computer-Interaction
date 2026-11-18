"""
검색 진입점. server의 질문 루프(Step 2)가 search_similar를 호출한다.

반환: [{"doc_id", "title", "url", "body", "score"}, ...]  (유사도 높은 순)
"""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable

from .embedder import Embedder
from .vector_db import INDEX_DIR, VectorDB


@lru_cache(maxsize=1)
def _embedder() -> Embedder:
    return Embedder()


@lru_cache(maxsize=1)
def _db() -> VectorDB:
    return VectorDB.load(INDEX_DIR)


def search_similar(query: str, exclude: Iterable[str] = (), k: int = 10) -> list[dict]:
    return _db().search(_embedder().embed_query(query), k=k, exclude=set(exclude))
