"""OpenDART에서 데모 회사의 공시 목록(과 원문)을 받아 data/disclosures/<종목코드>.json으로 저장한다.

사용법 (레포 루트에서):
    uv run python scripts/data/fetch_dart.py              # 목록만
    uv run python scripts/data/fetch_dart.py --with-text  # 원문 텍스트까지 (느림)

필요: .env의 DART_API_KEY (https://opendart.fss.or.kr 에서 무료 발급)
공시 제목으로 1차 분류(disclosure_type)만 하고, 실제 점수는 business-mcp 규칙이 계산한다.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BASE = "https://opendart.fss.or.kr/api"
ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "disclosures"
COMPANIES = ROOT / "data" / "demo_companies.json"

# 공시 제목 → disclosure_type. 위에서부터 먼저 맞는 것을 쓴다. DART 제목의 가운뎃점은 'ㆍ'.
TITLE_RULES: list[tuple[str, str]] = [
    (r"횡령[ㆍ·]?배임", "EMBEZZLEMENT"),
    (r"매매거래정지|거래정지", "TRADING_HALT"),
    (r"상장폐지", "DELISTING"),
    (r"관리종목", "ADMIN_ISSUE"),
    (r"감사보고서", "AUDIT_REPORT"),          # 감사의견은 원문에서 확인
    (r"(외부)?감사인.*(선임|변경|해임)", "AUDITOR_CHANGE"),
    (r"제출기한.*연장|미제출|제출지연", "FILING_DELAY"),
    (r"전환사채권발행결정|신주인수권부사채권발행결정", "CB_BW"),
    (r"유상증자결정", "RIGHTS_OFFERING"),
    (r"단일판매[ㆍ·]?공급계약.*(해지|정정)", "GOOD_NEWS_CANCEL"),
    (r"주식등의대량보유|최대주주등소유주식변동", "OWNERSHIP"),  # 담보 여부는 원문에서 확인
    (r"사업보고서|반기보고서|분기보고서", "PERIODIC_REPORT"),
]


def classify(title: str) -> str:
    for pattern, kind in TITLE_RULES:
        if re.search(pattern, title):
            return kind
    return "OTHER"


def api_key() -> str:
    key = os.getenv("DART_API_KEY")
    if not key:
        raise SystemExit("DART_API_KEY가 .env에 없습니다.")
    return key


def load_corp_codes(key: str) -> dict[str, str]:
    """종목코드 → DART 고유번호(corp_code)."""
    r = requests.get(f"{BASE}/corpCode.xml", params={"crtfc_key": key}, timeout=60)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        xml_bytes = z.read(z.namelist()[0])
    mapping = {}
    for item in ET.fromstring(xml_bytes).iter("list"):
        stock = (item.findtext("stock_code") or "").strip()
        if stock:
            mapping[stock] = item.findtext("corp_code").strip()
    return mapping


def month_chunks(start: date, end: date, days: int = 89):
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur, nxt
        cur = nxt + timedelta(days=1)


def list_disclosures(key: str, corp_code: str, start: date, end: date) -> list[dict]:
    rows: list[dict] = []
    for a, b in month_chunks(start, end):
        page = 1
        while True:
            r = requests.get(
                f"{BASE}/list.json",
                params={
                    "crtfc_key": key,
                    "corp_code": corp_code,
                    "bgn_de": a.strftime("%Y%m%d"),
                    "end_de": b.strftime("%Y%m%d"),
                    "page_no": page,
                    "page_count": 100,
                },
                timeout=30,
            )
            data = r.json()
            if data.get("status") == "013":  # 조회된 데이터 없음
                break
            if data.get("status") != "000":
                raise RuntimeError(f"DART 오류 {data.get('status')}: {data.get('message')}")
            rows.extend(data["list"])
            if page >= int(data.get("total_page", 1)):
                break
            page += 1
            time.sleep(0.2)
    return rows


def _decode(b: bytes) -> str:
    """DART 원문은 UTF-8이 대부분이지만 일부(옛 공시)는 CP949다."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp949", errors="ignore")


def fetch_text(key: str, rcept_no: str) -> str:
    r = requests.get(f"{BASE}/document.xml", params={"crtfc_key": key, "rcept_no": rcept_no}, timeout=60)
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            raw = "\n".join(_decode(z.read(n)) for n in z.namelist())
    except zipfile.BadZipFile:
        return ""
    raw = re.sub(r"<(style|script)\b.*?</\1>", " ", raw, flags=re.S | re.I)  # CSS·스크립트 제거
    text = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", text).strip()


def to_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-text", action="store_true", help="공시 원문 텍스트도 받기")
    parser.add_argument("--stock", help="한 회사만 (종목코드)")
    args = parser.parse_args()

    key = api_key()
    companies = json.loads(COMPANIES.read_text(encoding="utf-8"))
    corp_codes = load_corp_codes(key)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for c in companies["companies"]:
        if args.stock and c["stock_code"] != args.stock:
            continue
        corp_code = corp_codes.get(c["stock_code"])
        if not corp_code:
            # 상장폐지된 회사는 corpCode 목록에 종목코드가 비어 있을 수 있다 → 수동으로 demo_companies.json에 넣는다
            corp_code = c.get("corp_code")
        if not corp_code:
            print(f"[건너뜀] {c['name']}: corp_code를 찾지 못함. demo_companies.json에 직접 넣어 주세요.")
            continue
        c["corp_code"] = corp_code

        w = c["replay_window"]
        rows = list_disclosures(key, corp_code, to_date(w["from"]), to_date(w["to"]))
        out = []
        for row in sorted(rows, key=lambda x: x["rcept_dt"]):
            item = {
                "rcept_no": row["rcept_no"],
                "corp_code": corp_code,
                "stock_code": c["stock_code"],
                "corp_name": row["corp_name"],
                "title": row["report_nm"].strip(),
                "is_correction": "정정" in row["report_nm"],
                "disclosure_type": classify(row["report_nm"]),
                "filed_at": f"{row['rcept_dt'][:4]}-{row['rcept_dt'][4:6]}-{row['rcept_dt'][6:]}",
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={row['rcept_no']}",
            }
            if args.with_text and item["disclosure_type"] not in ("OTHER", "PERIODIC_REPORT"):
                item["body_text"] = fetch_text(key, row["rcept_no"])
                time.sleep(0.3)
            out.append(item)

        path = OUT_DIR / f"{c['stock_code']}.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        kinds = {}
        for o in out:
            kinds[o["disclosure_type"]] = kinds.get(o["disclosure_type"], 0) + 1
        print(f"[완료] {c['name']}: {len(out)}건 → {path.name}  {kinds}")

    COMPANIES.write_text(json.dumps(companies, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
