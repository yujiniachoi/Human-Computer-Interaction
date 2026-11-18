"""
네이버 블로그 최신 글 주소 수집기
블로그 ID를 넣으면 최신 글 N개의 주소를 db/urls.txt에 저장한다.
저장된 urls.txt는 crawl_posts.py에 그대로 넣으면 된다.

사용법:
    python db/crawler/collect_urls.py 블로그아이디 -n 20          # → db/urls.txt
    python db/crawler/collect_urls.py --id-file db/blog_ids.txt -n 20
    python db/crawler/crawl_posts.py                              # db/urls.txt → db/raw/
    (blog_ids.txt: 한 줄에 블로그 ID 하나)

동작 방식:
    1차: 네이버 블로그 RSS (https://rss.blog.naver.com/{blogId}.xml)
    2차: RSS가 막혀 있거나 개수가 부족하면 블로그 글 목록 페이지에서 보충
"""

import argparse
import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

DB_DIR = Path(__file__).resolve().parent.parent   # db/

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://blog.naver.com/",
}


def canonical(blog_id: str, log_no: str) -> str:
    return f"https://blog.naver.com/{blog_id}/{log_no}"


def from_rss(blog_id: str, session: requests.Session) -> list[str]:
    """RSS에서 최신 글 logNo 목록 (최신순)"""
    res = session.get(f"https://rss.blog.naver.com/{blog_id}.xml", headers=HEADERS, timeout=15)
    res.raise_for_status()
    root = ET.fromstring(res.content)
    log_nos = []
    for item in root.iter("item"):
        link = item.findtext("link") or item.findtext("guid") or ""
        m = re.search(r"blog\.naver\.com/[\w-]+/(\d+)", link) or re.search(r"logNo=(\d+)", link)
        if m:
            log_nos.append(m.group(1))
    return log_nos


def from_list_page(blog_id: str, n: int, session: requests.Session) -> list[str]:
    """블로그 글 목록 페이지에서 logNo 목록 (최신순). 공식 API가 아니라 구조가 바뀔 수 있음."""
    log_nos = []
    page = 1
    while len(log_nos) < n and page <= 10:
        res = session.get(
            "https://blog.naver.com/PostTitleListAsync.naver",
            params={"blogId": blog_id, "currentPage": page, "countPerPage": 30},
            headers=HEADERS,
            timeout=15,
        )
        res.raise_for_status()
        text = res.text.replace("\\'", "'")      # 응답에 JSON에 맞지 않는 이스케이프가 섞여 있음
        posts = json.loads(text).get("postList", [])
        if not posts:
            break
        log_nos += [str(p["logNo"]) for p in posts if "logNo" in p]
        page += 1
        time.sleep(1)
    return log_nos


def latest_urls(blog_id: str, n: int, session: requests.Session) -> list[str]:
    log_nos = []
    try:
        log_nos = from_rss(blog_id, session)
    except Exception as e:
        print(f"  RSS 실패 ({e}) → 글 목록 페이지로 시도")

    if len(log_nos) < n:
        try:
            log_nos += from_list_page(blog_id, n, session)
        except Exception as e:
            print(f"  글 목록 페이지 실패 ({e})")

    seen, result = set(), []
    for no in log_nos:                           # 중복 제거, 최신순 유지
        if no not in seen:
            seen.add(no)
            result.append(canonical(blog_id, no))
    return result[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("blog_ids", nargs="*", help="블로그 ID (여러 개 가능)")
    ap.add_argument("--id-file", help="블로그 ID 목록 파일 (한 줄에 하나)")
    ap.add_argument("-n", type=int, default=20, help="블로그당 가져올 글 수 (기본 20)")
    ap.add_argument("-o", "--out", default=str(DB_DIR / "urls.txt"), help="저장할 파일 (기본 db/urls.txt)")
    ap.add_argument("--append", action="store_true", help="기존 파일 뒤에 이어 쓰기 (중복 주소는 제외)")
    args = ap.parse_args()

    ids = list(args.blog_ids)
    if args.id_file:
        ids += [l.strip() for l in Path(args.id_file).read_text(encoding="utf-8").splitlines() if l.strip()]
    if not ids:
        ap.error("블로그 ID를 하나 이상 넣어 주세요.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if args.append and out.exists():
        existing = [l.strip() for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]

    session = requests.Session()
    all_urls = list(existing)
    for i, blog_id in enumerate(ids, 1):
        print(f"[{i}/{len(ids)}] {blog_id}")
        urls = latest_urls(blog_id, args.n, session)
        new = [u for u in urls if u not in all_urls]
        all_urls += new
        print(f"  {len(urls)}개 찾음, 새 주소 {len(new)}개")
        time.sleep(1.5)

    out.write_text("\n".join(all_urls) + "\n", encoding="utf-8")
    print(f"\n완료: 총 {len(all_urls)}개 주소 → {out}")


if __name__ == "__main__":
    main()
