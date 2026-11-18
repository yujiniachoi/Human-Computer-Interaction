"""
아주 단순한 벡터 DB: 글 임베딩 행렬(embeddings.npy) + 글 정보(docs.json)

글 수가 수천 개 수준이면 numpy 내적으로 충분히 빠르다.
더 커지면 이 파일만 FAISS/Chroma 등으로 바꾸면 된다.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

INDEX_DIR = Path(__file__).resolve().parent / "index"


class VectorDB:
    def __init__(self, docs: list[dict], embeddings: np.ndarray):
        """docs[i]는 embeddings[i]에 대응. 각 doc: {doc_id, title, url, body}"""
        assert len(docs) == len(embeddings), "docs와 embeddings 개수가 다릅니다"
        self.docs = docs
        self.embeddings = embeddings.astype(np.float32)

    # ---------- 저장 / 불러오기
    def save(self, index_dir: Path = INDEX_DIR):
        index_dir.mkdir(parents=True, exist_ok=True)
        np.save(index_dir / "embeddings.npy", self.embeddings)
        (index_dir / "docs.json").write_text(
            json.dumps(self.docs, ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, index_dir: Path = INDEX_DIR) -> "VectorDB":
        if not (index_dir / "docs.json").exists():
            raise FileNotFoundError(f"인덱스가 없습니다: {index_dir} (python -m rag.build_index 먼저 실행)")
        docs = json.loads((index_dir / "docs.json").read_text(encoding="utf-8"))
        return cls(docs, np.load(index_dir / "embeddings.npy"))

    # ---------- 검색
    def search(self, query_vec: np.ndarray, k: int = 10, exclude: set[str] = frozenset()) -> list[dict]:
        """코사인 유사도 높은 순으로, exclude에 없는 글 최대 k개."""
        if not self.docs:
            return []
        scores = self.embeddings @ query_vec.astype(np.float32)
        results = []
        for i in np.argsort(-scores):
            doc = self.docs[i]
            if doc["doc_id"] in exclude:
                continue
            results.append({**doc, "score": float(scores[i])})
            if len(results) == k:
                break
        return results
