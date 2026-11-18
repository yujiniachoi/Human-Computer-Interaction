# rag — 블로그 DB 벡터 검색

`db/processed/clean.json`의 글을 임베딩해 벡터 DB로 만들고, 질문 루프 Step 2에서 비슷한 글을 찾습니다.

| 파일 | 역할 |
|---|---|
| `embedder.py` | 텍스트 → 벡터. 로컬 `sentence-transformers` 모델 (기본 `jhgan/ko-sroberta-multitask`, `EMBED_MODEL`로 변경). 긴 글은 약 300자 청크로 나눠 평균 |
| `vector_db.py` | `index/embeddings.npy` + `index/docs.json`. 코사인 유사도 순 검색, 이미 쓴 글 제외 |
| `build_index.py` | clean.json → 벡터 DB 생성 |
| `retrieve.py` | `search_similar(query, exclude, k)` — server가 호출 |
| `index/` | 생성된 벡터 DB (git 제외) |

```sh
python -m rag.build_index     # 저장소 루트에서, clean.json이 바뀔 때마다
```

doc_id는 네이버 글 번호로 만듭니다(`doc_{logNo}`). 그래서 인덱스를 다시 만들어도 같은 글은 같은 id를 씁니다.
