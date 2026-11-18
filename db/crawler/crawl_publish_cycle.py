"""
네이버 블로거별 발행 이력 크롤러 (발행 주기 분석용)
블로그 ID 목록을 넣으면 블로거마다 최신 글 N개(기본 30개)의
아이디, 발행 날짜/시간, 제목을 모아 JSON 하나로 저장한다.

사용법:
    python db/crawler/crawl_publish_cycle.py              # db/blog_ids.txt 사용 → db/processed/publish_history.json
    python db/crawler/crawl_publish_cycle.py mateA mateB -n 30
    python db/crawler/crawl_publish_cycle.py --id-file db/blog_ids.txt -o publish_history.json
    (blog_ids.txt: 한 줄에 블로그 ID 하나. 블로그 주소를 통째로 넣어도 됨)

동작 방식 (블로거 1명당):
    1차: RSS(https://rss.blog.naver.com/{id}.xml) → 제목 + 발행 시각을 한 번에 가져옴
    2차: RSS에 글이 N개보다 적으면 글 목록 페이지에서 나머지 글 번호를 찾고,
         그 글들만 하나씩 열어서 발행 시각을 읽음 (기존 크롤러와 같은 방식)

출력(JSON):
{
  "crawled_at": "...",
  "bloggers": {
    "mateA": {
      "stats": {"count": 30, "first": "...", "last": "...",
                "avg_interval_days": 2.4, "median_interval_days": 1.9},
      "posts": [{"blog_id", "log_no", "title", "published_at", "url"}, ...]  # 최신순
    }
  },
  "failed": ["..."]
}
"""

import argparse
import json
import re
import statistics
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote_plus

import requests
from bs4 import BeautifulSoup

DB_DIR = Path(__file__).resolve().parent.parent   # db/

KST = timezone(timedelta(hours=9))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://blog.naver.com/",
}
INVISIBLE = re.compile(r"[​‌‍﻿]")


def clean(text: str) -> str:
    return INVISIBLE.sub("", text or "").replace("\xa0", " ").strip()


def post_url(blog_id: str, log_no: str) -> str:
    return f"https://blog.naver.com/{blog_id}/{log_no}"


# ------------------------------------------------------------ 날짜 (기존 크롤러와 동일)
def parse_date(raw: str, now: datetime) -> str:
    """'2026. 11. 3. 7:50', 'n시간 전', 'n분 전', '어제' 등 → ISO 8601 (+09:00)"""
    raw = clean(raw)
    m = re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{1,2}):(\d{2})", raw)
    if m:
        y, mo, d, h, mi = map(int, m.groups())
        return datetime(y, mo, d, h, mi, tzinfo=KST).isoformat()
    m = re.search(r"(\d+)\s*분\s*전", raw)
    if m:
        return (now - timedelta(minutes=int(m.group(1)))).replace(second=0, microsecond=0).isoformat()
    m = re.search(r"(\d+)\s*시간\s*전", raw)
    if m:
        return (now - timedelta(hours=int(m.group(1)))).replace(second=0, microsecond=0).isoformat()
    if "방금" in raw:
        return now.replace(second=0, microsecond=0).isoformat()
    if "어제" in raw:
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    m = re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?", raw)        # 시간 없이 날짜만 있는 경우
    if m:
        y, mo, d = map(int, m.groups())
        return datetime(y, mo, d, tzinfo=KST).isoformat()
    return raw


# ------------------------------------------------------------ 1차: RSS
def from_rss(blog_id: str, session: requests.Session) -> list[dict]:
    res = session.get(f"https://rss.blog.naver.com/{blog_id}.xml", headers=HEADERS, timeout=15)
    res.raise_for_status()
    root = ET.fromstring(res.content)
    posts = []
    for item in root.iter("item"):
        link = item.findtext("link") or item.findtext("guid") or ""
        m = re.search(r"blog\.naver\.com/[\w-]+/(\d+)", link) or re.search(r"logNo=(\d+)", link)
        if not m:
            continue
        published = ""
        pub = item.findtext("pubDate")
        if pub:
            try:
                published = parsedate_to_datetime(pub).astimezone(KST).isoformat()
            except Exception:
                published = clean(pub)
        posts.append({
            "blog_id": blog_id,
            "log_no": m.group(1),
            "title": clean(item.findtext("title")),
            "published_at": published,
            "url": post_url(blog_id, m.group(1)),
        })
    return posts


# ------------------------------------------------------------ 2차: 글 목록 + 개별 글
def list_log_nos(blog_id: str, n: int, session: requests.Session, delay: float) -> list[dict]:
    """글 목록 페이지에서 글 번호와 제목 (최신순). 공식 API가 아니라 구조가 바뀔 수 있음."""
    found, page = [], 1
    while len(found) < n and page <= 10:
        res = session.get(
            "https://blog.naver.com/PostTitleListAsync.naver",
            params={"blogId": blog_id, "currentPage": page, "countPerPage": 30},
            headers=HEADERS,
            timeout=15,
        )
        res.raise_for_status()
        data = json.loads(res.text.replace("\\'", "'"))   # 응답에 JSON에 맞지 않는 이스케이프가 섞여 있음
        items = data.get("postList", [])
        if not items:
            break
        for p in items:
            if "logNo" in p:
                found.append({"log_no": str(p["logNo"]),
                              "title": clean(unquote_plus(p.get("title", "")))})
        page += 1
        time.sleep(delay)
    return found


def fetch_post_meta(blog_id: str, log_no: str, session: requests.Session) -> dict:
    """글 하나를 열어 제목과 발행 시각 읽기 (기존 크롤러의 parse_post에서 필요한 부분만)"""
    url = f"https://blog.naver.com/PostView.naver?blogId={blog_id}&logNo={log_no}"
    res = session.get(url, headers=HEADERS, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    title_el = (soup.select_one(".se-title-text") or soup.select_one(".pcol1 .htitle")
                or soup.select_one(".se_title .se_textarea"))
    date_el = soup.select_one(".se_publishDate") or soup.select_one(".date")
    return {
        "blog_id": blog_id,
        "log_no": log_no,
        "title": clean(title_el.get_text(" ")) if title_el else "",
        "published_at": parse_date(date_el.get_text(), datetime.now(KST)) if date_el else "",
        "url": post_url(blog_id, log_no),
    }


# ------------------------------------------------------------ 블로거 1명 처리
def crawl_blogger(blog_id: str, n: int, session: requests.Session, delay: float) -> list[dict]:
    posts = []
    try:
        posts = from_rss(blog_id, session)
        print(f"  RSS: {len(posts)}개")
    except Exception as e:
        print(f"  RSS 실패 ({e})")

    if len(posts) < n:
        have = {p["log_no"] for p in posts}
        try:
            listed = list_log_nos(blog_id, n, session, delay)
        except Exception as e:
            print(f"  글 목록 실패 ({e})")
            listed = []
        missing = [x for x in listed if x["log_no"] not in have][: n - len(posts)]
        if missing:
            print(f"  RSS에 없는 {len(missing)}개는 글을 하나씩 열어서 확인")
        for i, x in enumerate(missing, 1):
            try:
                meta = fetch_post_meta(blog_id, x["log_no"], session)
                meta["title"] = meta["title"] or x["title"]
                posts.append(meta)
            except Exception as e:
                print(f"    [{i}/{len(missing)}] {x['log_no']} 실패: {e}")
            time.sleep(delay)

    # 중복 제거 + 최신순 정렬 + N개
    uniq = {p["log_no"]: p for p in posts}
    ordered = sorted(uniq.values(), key=lambda p: (p["published_at"], int(p["log_no"])), reverse=True)
    return ordered[:n]


# ------------------------------------------------------------ 발행 주기 통계
def publish_stats(posts: list[dict]) -> dict:
    times = []
    for p in posts:
        try:
            times.append(datetime.fromisoformat(p["published_at"]))
        except (ValueError, TypeError):
            pass
    times.sort()
    gaps = [(b - a).total_seconds() / 86400 for a, b in zip(times, times[1:])]
    return {
        "count": len(posts),
        "dated": len(times),
        "first": times[0].isoformat() if times else None,
        "last": times[-1].isoformat() if times else None,
        "avg_interval_days": round(statistics.mean(gaps), 2) if gaps else None,
        "median_interval_days": round(statistics.median(gaps), 2) if gaps else None,
    }


# ------------------------------------------------------------ 실행
def read_ids(args, parser) -> list[str]:
    ids = list(args.blog_ids)
    if args.id_file:
        ids += Path(args.id_file).read_text(encoding="utf-8").split()
    if not ids:
        default_file = DB_DIR / "blog_ids.txt"
        if default_file.exists():
            ids = default_file.read_text(encoding="utf-8").split()
            print(f"{default_file.name}에서 블로그 ID {len(ids)}개를 읽었습니다.")
    if not ids:
        ids = input("블로그 ID를 입력하세요 (여러 개면 띄어쓰기로 구분): ").split()
    if not ids:
        parser.error("블로그 ID를 하나 이상 넣어 주세요.")
    # 블로그 주소를 통째로 넣어도 ID만 사용
    ids = [re.sub(r"^https?://(m\.)?blog\.naver\.com/", "", i).strip("/").split("/")[0] for i in ids]
    return list(dict.fromkeys(ids))      # 중복 ID 제거, 순서 유지


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("blog_ids", nargs="*", help="블로그 ID (여러 개 가능)")
    ap.add_argument("--id-file", help="블로그 ID 목록 파일 (한 줄에 하나)")
    ap.add_argument("-n", type=int, default=30, help="블로거당 글 수 (기본 30)")
    ap.add_argument("-o", "--out", default=str(DB_DIR / "processed" / "publish_history.json"), help="저장할 JSON 파일 (기본 db/processed/publish_history.json)")
    ap.add_argument("--delay", type=float, default=1.5, help="요청 간격(초)")
    args = ap.parse_args()

    ids = read_ids(args, ap)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    result = {"crawled_at": datetime.now(KST).isoformat(), "n_per_blogger": args.n,
              "bloggers": {}, "failed": []}

    session = requests.Session()
    for i, blog_id in enumerate(ids, 1):
        print(f"[{i}/{len(ids)}] {blog_id}")
        posts = crawl_blogger(blog_id, args.n, session, args.delay)
        if not posts:
            result["failed"].append(blog_id)
            print("  글을 하나도 가져오지 못했습니다.")
        else:
            stats = publish_stats(posts)
            result["bloggers"][blog_id] = {"stats": stats, "posts": posts}
            print(f"  {len(posts)}개 저장 · 평균 간격 {stats['avg_interval_days']}일")
        # 블로거 하나 끝날 때마다 저장 (중간에 끊겨도 진행분 유지)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(args.delay)

    print(f"\n완료: 성공 {len(result['bloggers'])}명, 실패 {len(result['failed'])}명 → {out}")
    if result["failed"]:
        print("실패한 ID: " + ", ".join(result["failed"]))


if __name__ == "__main__":
    main()
