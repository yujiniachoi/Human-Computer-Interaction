"""
네이버 블로그 최신 '푸드 후기' 글 주소 수집기
블로그 ID를 넣으면 최신 글을 차례로 훑어서
맛집 / 카페 / 디저트의 리뷰·방문 후기 글만 N개 골라 db/urls.txt에 저장한다.
저장된 urls.txt는 crawl_posts.py에 그대로 넣으면 된다.

사용법:
    python db/crawler/collect_food_urls.py 블로그아이디 -n 20                      # → db/urls.txt
    python db/crawler/collect_food_urls.py --id-file db/blog_ids.txt -n 20   (blog_ids.txt: 한 줄에 ID 하나)
    python db/crawler/crawl_posts.py                                          # db/urls.txt → db/raw/

옵션:
    --max-scan 100   블로그당 최신글 몇 개까지 훑을지 (조건에 맞는 글이 적으면 여기서 멈춤)
    --title-only     본문을 열지 않고 제목/카테고리만으로 판별 (빠르지만 덜 정확)
    --all            필터 없이 최신글 N개 그대로 (예전 동작)

동작 방식:
    1) 글 목록: RSS (https://rss.blog.naver.com/{blogId}.xml) → 부족하면 글 목록 페이지에서 보충
    2) 필터:
       - 제목/카테고리에 '레시피·집밥·홈카페' 등이 있으면 제외
       - 기본 모드: 글 본문을 열어서
           푸드 글인가?   제목/카테고리에 맛집·카페·디저트 단어 or 본문에 3개 이상
           후기 글인가?   지도(장소) 첨부 or 방문·후기·웨이팅·주차 같은 단어 2개 이상
       - --title-only: 제목/카테고리에 맛집·카페·디저트 단어가 있으면 통과
"""

import argparse
import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote_plus

import requests
from bs4 import BeautifulSoup

DB_DIR = Path(__file__).resolve().parent.parent   # db/

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://blog.naver.com/",
}

# ---------------------------------------------------------------------------
# 판별용 단어 목록 (필요하면 여기만 고치면 됨)
# ---------------------------------------------------------------------------
FOOD_TERMS = (
    # 맛집
    "맛집", "식당", "레스토랑", "음식점", "오마카세", "고기", "삼겹살", "갈비", "한우", "곱창", "초밥",
    "스시", "라멘", "우동", "파스타", "피자", "버거", "국밥", "냉면", "칼국수", "떡볶이", "치킨",
    "족발", "보쌈", "횟집", "포차", "이자카야", "술집", "와인바", "다이닝", "쌀국수", "마라탕",
    "짬뽕", "짜장", "돈까스", "돈카츠", "샤브샤브", "뷔페", "코스요리", "한식", "중식", "일식", "양식",
    "브런치", "점심", "저녁", "런치", "디너", "먹방",
    # 카페
    "카페", "커피", "라떼", "에스프레소", "핸드드립", "티룸", "찻집",
    # 디저트
    "디저트", "베이커리", "빵집", "케이크", "케잌", "빙수", "마카롱", "크로플", "스콘", "휘낭시에",
    "도넛", "젤라또", "아이스크림", "타르트", "쿠키", "베이글", "소금빵", "크루아상", "푸딩",
)
VISIT_TERMS = (
    "방문", "다녀왔", "다녀온", "다녀옴", "후기", "리뷰", "웨이팅", "대기", "영업시간", "운영시간",
    "브레이크타임", "주차", "위치", "주소", "내돈내산", "재방문", "메뉴판", "가봤", "가본", "매장",
    "예약", "좌석", "인테리어", "분위기", "가격", "주문", "솔직", "찾아갔", "들렀",
)
EXCLUDE_TERMS = (  # 집에서 만드는 글 등 '리뷰/방문'이 아닌 푸드 글
    "레시피", "만들기", "만드는법", "만드는 법", "집밥", "홈쿡", "홈카페", "밀키트", "요리법",
    "에어프라이어", "도시락 싸",
)
# 스마트에디터 '장소(지도)' 컴포넌트 → 방문 후기의 강한 신호
MAP_SELECTOR = ".se-placesMap, .se-map, .se_map, [class*='placesMap'], [class*='se-module-map']"


def canonical(blog_id: str, log_no: str) -> str:
    return f"https://blog.naver.com/{blog_id}/{log_no}"


def hits(text: str, terms) -> set:
    return {t for t in terms if t in text}


# ---------------------------------------------------------------------------
# 1) 글 목록 (제목/카테고리 포함)
# ---------------------------------------------------------------------------
def from_rss(blog_id: str, session: requests.Session) -> list[dict]:
    """RSS에서 최신 글 목록 (최신순). 보통 최근 50개 정도."""
    res = session.get(f"https://rss.blog.naver.com/{blog_id}.xml", headers=HEADERS, timeout=15)
    res.raise_for_status()
    root = ET.fromstring(res.content)
    posts = []
    for item in root.iter("item"):
        link = item.findtext("link") or item.findtext("guid") or ""
        m = re.search(r"blog\.naver\.com/[\w-]+/(\d+)", link) or re.search(r"logNo=(\d+)", link)
        if m:
            posts.append({
                "log_no": m.group(1),
                "title": (item.findtext("title") or "").strip(),
                "category": (item.findtext("category") or "").strip(),
            })
    return posts


def from_list_page(blog_id: str, max_scan: int, session: requests.Session) -> list[dict]:
    """블로그 글 목록 페이지에서 글 목록 (최신순). 공식 API가 아니라 구조가 바뀔 수 있음."""
    posts = []
    page = 1
    while len(posts) < max_scan and page <= 20:
        res = session.get(
            "https://blog.naver.com/PostTitleListAsync.naver",
            params={"blogId": blog_id, "currentPage": page, "countPerPage": 30},
            headers=HEADERS,
            timeout=15,
        )
        res.raise_for_status()
        text = res.text.replace("\\'", "'")      # 응답에 JSON에 맞지 않는 이스케이프가 섞여 있음
        items = json.loads(text).get("postList", [])
        if not items:
            break
        for p in items:
            if "logNo" in p:
                posts.append({
                    "log_no": str(p["logNo"]),
                    "title": unquote_plus(str(p.get("title", ""))).strip(),
                    "category": "",
                })
        page += 1
        time.sleep(1)
    return posts


def recent_posts(blog_id: str, max_scan: int, session: requests.Session) -> list[dict]:
    posts = []
    try:
        posts = from_rss(blog_id, session)
    except Exception as e:
        print(f"  RSS 실패 ({e}) → 글 목록 페이지로 시도")

    if len(posts) < max_scan:
        try:
            posts += from_list_page(blog_id, max_scan, session)
        except Exception as e:
            print(f"  글 목록 페이지 실패 ({e})")

    seen, result = set(), []
    for p in posts:                              # 중복 제거, 최신순 유지 (RSS 정보 우선)
        if p["log_no"] not in seen:
            seen.add(p["log_no"])
            result.append(p)
    return result[:max_scan]


# ---------------------------------------------------------------------------
# 2) 푸드 후기 판별
# ---------------------------------------------------------------------------
def fetch_body(blog_id: str, log_no: str, session: requests.Session) -> tuple[str, bool]:
    """(본문 텍스트, 지도 첨부 여부)"""
    res = session.get(
        f"https://blog.naver.com/PostView.naver?blogId={blog_id}&logNo={log_no}",
        headers=HEADERS, timeout=15,
    )
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    container = (
        soup.select_one(".se-main-container")
        or soup.select_one("#postViewArea")
        or soup.select_one(".se_component_wrap")
    )
    text = container.get_text(" ") if container else ""
    return text, soup.select_one(MAP_SELECTOR) is not None


def check_head(title: str, category: str) -> tuple[str, str]:
    """제목/카테고리만 보고 1차 판정 → ("exclude" | "food" | "unknown", 사유)"""
    head = f"{title} {category}"
    ex = hits(head, EXCLUDE_TERMS)
    if ex:
        return "exclude", f"집에서 만드는 글({', '.join(sorted(ex))})"
    food = hits(head, FOOD_TERMS)
    if food:
        return "food", f"제목/카테고리({', '.join(sorted(food)[:3])})"
    return "unknown", "제목/카테고리에 푸드 단어 없음"


def check_body(title: str, category: str, body: str, has_map: bool) -> tuple[bool, str]:
    """본문까지 보고 최종 판정"""
    head = f"{title} {category}"
    if hits(head, EXCLUDE_TERMS) and not has_map:
        return False, "집에서 만드는 글"
    food_head, food_body = hits(head, FOOD_TERMS), hits(body, FOOD_TERMS)
    if not food_head and len(food_body) < 3:
        return False, "푸드 글 아님"
    if len(hits(body, EXCLUDE_TERMS)) >= 2 and not has_map:
        return False, "레시피 위주"
    if has_map:
        return True, "푸드 + 지도 첨부"
    visit = hits(body, VISIT_TERMS)
    if len(visit) >= 2:
        return True, f"푸드 + 방문/후기({', '.join(sorted(visit)[:4])})"
    return False, "푸드지만 후기 신호 부족"


def latest_food_urls(blog_id: str, n: int, max_scan: int, mode: str,
                     session: requests.Session, skip: set) -> list[str]:
    posts = recent_posts(blog_id, max_scan, session)
    print(f"  최신글 {len(posts)}개 확인")
    result = []
    for p in posts:
        if len(result) >= n:
            break
        url = canonical(blog_id, p["log_no"])
        title = p["title"][:30]

        if mode == "all":
            result.append(url)
            continue
        if url in skip:                          # 이미 urls.txt에 있는 주소 (--append)
            result.append(url)
            continue

        verdict, reason = check_head(p["title"], p["category"])
        if verdict == "exclude" or (mode == "title" and verdict != "food"):
            print(f"    제외 {p['log_no']} {title} ({reason})")
            continue
        if mode == "title":
            ok = True
        else:
            try:
                body, has_map = fetch_body(blog_id, p["log_no"], session)
                ok, reason = check_body(p["title"], p["category"], body, has_map)
            except Exception as e:
                print(f"    실패 {p['log_no']} {title} ({e})")
                continue
            time.sleep(1)
        print(f"    {'통과' if ok else '제외'} {p['log_no']} {title} ({reason})")
        if ok:
            result.append(url)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("blog_ids", nargs="*", help="블로그 ID (여러 개 가능)")
    ap.add_argument("--id-file", help="블로그 ID 목록 파일 (한 줄에 하나)")
    ap.add_argument("-n", type=int, default=20, help="블로그당 가져올 푸드 후기 글 수 (기본 20)")
    ap.add_argument("--max-scan", type=int, default=100, help="블로그당 최대 몇 개 최신글까지 훑을지 (기본 100)")
    ap.add_argument("-o", "--out", default=str(DB_DIR / "urls.txt"), help="저장할 파일 (기본 db/urls.txt)")
    ap.add_argument("--append", action="store_true", help="기존 파일 뒤에 이어 쓰기 (중복 주소는 제외)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--title-only", action="store_true", help="본문을 열지 않고 제목/카테고리로만 판별")
    g.add_argument("--all", action="store_true", help="필터 없이 최신글 N개 (예전 동작)")
    args = ap.parse_args()
    mode = "all" if args.all else "title" if args.title_only else "body"

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
    summary = []
    for i, blog_id in enumerate(ids, 1):
        print(f"[{i}/{len(ids)}] {blog_id}")
        urls = latest_food_urls(blog_id, args.n, max(args.max_scan, args.n), mode, session, set(existing))
        new = [u for u in urls if u not in all_urls]
        all_urls += new
        print(f"  → {len(urls)}개 찾음, 새 주소 {len(new)}개")
        if len(urls) < args.n:
            print(f"  ※ 조건에 맞는 글이 {args.n}개보다 적음 (--max-scan을 늘리면 더 찾아봄)")
        summary.append((blog_id, len(urls)))
        time.sleep(1.5)

    out.write_text("\n".join(all_urls) + "\n", encoding="utf-8")
    print(f"\n완료: 총 {len(all_urls)}개 주소 → {out}")
    for blog_id, cnt in summary:
        print(f"  {blog_id}: {cnt}개")


if __name__ == "__main__":
    main()
