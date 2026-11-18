"""
db/processed/clean.json → 벡터 DB(rag/index/) 생성

사용법 (저장소 루트에서):
    python -m rag.build_index
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from .embedder import Embedder
from .vector_db import INDEX_DIR, VectorDB

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "db" / "processed" / "clean.json"


def make_doc_id(post: dict, i: int) -> str:
    """네이버 글 URL의 글 번호(logNo)로 안정적인 id를 만든다. 없으면 순번."""
    m = re.search(r"(?:logNo=|/)(\d{6,})", post.get("url", ""))
    return f"doc_{m.group(1)}" if m else f"doc_{i}"


def main():
    posts = json.loads(SOURCE.read_text(encoding="utf-8"))
    print(f"글 {len(posts)}개 로드: {SOURCE}")

    docs, seen = [], set()
    for i, p in enumerate(posts):
        doc_id = make_doc_id(p, i)
        if doc_id in seen:
            continue
        seen.add(doc_id)
        docs.append({"doc_id": doc_id, "title": p.get("title", ""),
                     "url": p.get("url", ""), "body": p.get("body", "")})

    embedder = Embedder()
    vecs = []
    for n, d in enumerate(docs, 1):
        vecs.append(embedder.embed_document(f"{d['title']}\n{d['body']}"))
        if n % 50 == 0:
            print(f"  {n}/{len(docs)} 임베딩 완료")

    VectorDB(docs, np.stack(vecs) if vecs else np.zeros((0, 1), np.float32)).save(INDEX_DIR)
    print(f"저장 완료: {INDEX_DIR} (글 {len(docs)}개)")


if __name__ == "__main__":
    main()
