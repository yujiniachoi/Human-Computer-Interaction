# db — 블로그 포스팅 DB

RAG가 참고할 "질 좋은" 네이버 블로그 글을 모으고 정리하는 곳입니다.

## 폴더

| 경로 | 내용 | git |
|---|---|---|
| `crawler/` | 수집·전처리 스크립트 | O |
| `blog_ids.txt` | 수집할 블로그 ID 목록 (한 줄에 하나) | O |
| `urls.txt` | 수집할 글 URL 목록 (스크립트가 생성) | X |
| `raw/` | 크롤링 원본 `{logNo}.json` | X |
| `processed/` | 전처리 결과 `clean.json`, `clean.csv`, `publish_history.json` | 팀에서 결정 |

## 실행 순서 (저장소 루트에서)

```sh
pip install requests beautifulsoup4 pandas
python db/crawler/collect_food_urls.py --id-file db/blog_ids.txt -n 20   # → db/urls.txt
python db/crawler/crawl_posts.py                                         # → db/raw/*.json
python db/crawler/preprocess.py                                          # → db/processed/clean.json
python db/crawler/crawl_publish_cycle.py                                 # → db/processed/publish_history.json
```

푸드 필터 없이 최신 글을 모으려면 `collect_food_urls.py` 대신 `collect_urls.py`를 씁니다.

## 스키마

`raw/{logNo}.json`
```json
{"blog_id": "", "title": "", "published_at": "", "url": "", "body": ["줄 단위", ""]}
```

`processed/clean.json` (레코드 배열)
```json
{"blog_id": "", "title": "", "published_at": "", "url": "", "body": "한 줄로 합친 본문",
 "region": "", "hours": "", "prices": [], "sponsored": false}
```

## "질 좋은 글" 선정 기준

<!-- TODO: 팀에서 정한 기준을 적어 주세요 (예: 이웃 수, 공감/댓글 수, 본문 길이, 협찬 여부 등) -->
