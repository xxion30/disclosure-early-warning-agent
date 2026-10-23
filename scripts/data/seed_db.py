"""JSON → SQLite. data/app.db를 (다시) 만들고 PB·고객·보유 종목·회사·공시를 채운다.

사용법 (레포 루트에서):
    uv run python scripts/data/seed_db.py          # 비어 있는 테이블만 새로 만들고 채움 (이미 있으면 덮어씀)
    uv run python scripts/data/seed_db.py --reset  # 모든 테이블을 지우고 처음부터

공시 목록은 data/disclosures/<종목코드>.json에서 읽는다. 원문(body_text)은 테스트가 실제로 쓰는 공시 6건에만 남아 있고(나머지는 비움),
비어 있으면 rag_docs/companies/ 의 같은 rcept_no 원문 md로 채운다. 새 공시 수집은 collector(`python -m collector --once`)가 한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import bcrypt

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared.db import get_engine, get_session, init_db  # noqa: E402
from shared.models import Base, Company, Customer, Disclosure, Holding, PB  # noqa: E402

DATA = ROOT / "data"
RAG_COMPANIES = ROOT / "rag_docs" / "companies"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def rag_bodies() -> dict[str, str]:
    """rag_docs/companies/**/*.md 머리말의 rcept_no → 본문."""
    bodies = {}
    for path in RAG_COMPANIES.glob("*/*.md"):
        text = path.read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
        if not m:
            continue
        rcept = re.search(r'rcept_no:\s*"?(\d+)"?', m.group(1))
        if rcept:
            bodies[rcept.group(1)] = m.group(2).strip()
    return bodies


def seed_people(s) -> None:
    cust = load_json(DATA / "seed" / "customers.json")
    for pb in cust["pbs"]:
        hashed = bcrypt.hashpw(pb["password"].encode(), bcrypt.gensalt()).decode()
        s.merge(PB(pb_id=pb["pb_id"], name=pb["name"], email=pb["email"], password_hash=hashed))
    for c in cust["customers"]:
        s.merge(Customer(**{k: c[k] for k in ("customer_id", "pb_id", "name", "risk_profile", "total_assets")}))
    for h in cust["holdings"]:
        s.merge(Holding(**{k: h[k] for k in ("customer_id", "stock_code", "eval_amount", "on_credit")}))
    print(f"[PB·고객] PB {len(cust['pbs'])}명, 고객 {len(cust['customers'])}명, 보유 {len(cust['holdings'])}건")


def seed_companies(s) -> dict[str, str]:
    """회사 적재. 반환: 종목코드 → corp_code."""
    corp_of = {}
    for c in load_json(DATA / "demo_companies.json")["companies"]:
        exp = c.get("expected") or {}
        corp_code = c.get("corp_code")
        if not corp_code:
            corp_code = c["stock_code"]  # 임시 키. data/demo_companies.json에 corp_code를 채운 뒤 --reset으로 다시 적재
            print(f"  ! {c['name']}: corp_code 없음 → 종목코드를 임시 키로 사용 (corp_code를 채운 뒤 재적재)")
        base_score = c.get("base_score", exp.get("base_score"))
        if base_score is None:
            base_score = 0
            print(f"  ! {c['name']}: base_score 미확정 → 0 (TODO(확인 필요): 사업보고서로 계산)")
        s.merge(
            Company(
                corp_code=corp_code,
                stock_code=c["stock_code"],
                name=c["name"],
                market=c.get("market"),
                base_score=base_score,
                base_reasons=c.get("base_reasons", exp.get("base_reasons", [])),
            )
        )
        corp_of[c["stock_code"]] = corp_code
    print(f"[회사] {len(corp_of)}개")
    return corp_of


def seed_disclosures(s, corp_of: dict[str, str]) -> None:
    bodies = rag_bodies()
    total = 0
    for path in sorted((DATA / "disclosures").glob("*.json")):
        stock = path.stem
        if stock not in corp_of:
            continue
        for d in load_json(path):
            s.merge(
                Disclosure(
                    rcept_no=d["rcept_no"],
                    corp_code=corp_of[stock],
                    title=d["title"],
                    disclosure_type=d.get("disclosure_type"),
                    filed_at=d["filed_at"],
                    url=d.get("url"),
                    body_text=d.get("body_text") or bodies.get(d["rcept_no"]),
                    processed=False,
                )
            )
            total += 1
    if total == 0:
        print("[공시] 0건 — data/disclosures/가 비어 있음. git에서 받아 오거나 `python -m collector --once`로 수집하세요.")
    else:
        print(f"[공시] {total}건")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="모든 테이블을 지우고 다시 만든다")
    args = parser.parse_args()

    if args.reset:
        Base.metadata.drop_all(get_engine())
    init_db()
    with get_session() as s:
        seed_people(s)
        corp_of = seed_companies(s)
        seed_disclosures(s, corp_of)
    print("완료: data/app.db")


if __name__ == "__main__":
    main()
