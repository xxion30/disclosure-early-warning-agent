"""데모 회사 공시를 날짜순으로 api(POST /runs)에 흘려보내고, 등급이 어떻게 변하는지 출력한다.

사용법 (api 8000, business-mcp 8001, rag-mcp 8002가 떠 있어야 함):
    uv run python scripts/replay_demo.py --stock 001570
    uv run python scripts/replay_demo.py --stock 001570 --dry-run   # 보낼 순서만 출력

끝에 "첫 High 이상 날짜 / 사건일 / 며칠 먼저"를 출력한다 (시나리오 A 3번 확인).
승인 대기(WAITING_APPROVAL)에서 멈춘 공시는 PB 결정이 필요하므로 거기서 재생을 멈춘다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LEVEL_ORDER = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
FINISHED = {"DONE", "FAILED", "MANUAL_CHECK", "WAITING_APPROVAL"}


def load(stock: str) -> tuple[dict, list[dict]]:
    companies = json.loads((ROOT / "data" / "demo_companies.json").read_text(encoding="utf-8"))["companies"]
    company = next((c for c in companies if c["stock_code"] == stock), None)
    if not company:
        raise SystemExit(f"demo_companies.json에 {stock}가 없습니다.")
    path = ROOT / "data" / "disclosures" / f"{stock}.json"
    if not path.exists():
        raise SystemExit(f"{path} 없음. 먼저 scripts/fetch_dart.py를 실행하세요.")
    disclosures = sorted(json.loads(path.read_text(encoding="utf-8")), key=lambda d: (d["filed_at"], d["rcept_no"]))
    return company, disclosures


def assessment_of(rcept_no: str) -> dict | None:
    from sqlalchemy import select

    from shared.db import get_session
    from shared.models import RiskAssessment

    with get_session() as s:
        row = s.scalars(
            select(RiskAssessment).where(RiskAssessment.rcept_no == rcept_no).order_by(RiskAssessment.assessment_id.desc())
        ).first()
        if not row:
            return None
        return {"status": row.status, "level": row.level, "total_score": row.total_score}


def wait_for(rcept_no: str, timeout: float) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        a = assessment_of(rcept_no)
        if a and a["status"] in FINISHED:
            return a
        time.sleep(1)
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", required=True, help="종목코드 (예: 001570)")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=150, help="공시 1건당 대기 초")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--until-event", action="store_true", help="사건일 이전 공시까지만 흘려보냄")
    args = parser.parse_args()

    company, disclosures = load(args.stock)
    event = company.get("event") or {}
    event_date = (event.get("date") or "")[:10]
    if args.until_event and len(event_date) == 10:
        disclosures = [d for d in disclosures if d["filed_at"] < event_date]
    if not event.get("verified"):
        print(f"[주의] {company['name']} 사건일({event.get('date')})은 아직 verified=false 입니다. 결과는 참고용.")

    print(f"{company['name']}({args.stock}): 공시 {len(disclosures)}건 재생")
    first_alert: tuple[str, str, str] | None = None  # (날짜, 등급, rcept_no)

    for d in disclosures:
        tag = f"{d['filed_at']} {d['rcept_no']} {d['title'][:30]}"
        if args.dry_run:
            print(f"  (dry-run) {tag}")
            continue
        r = requests.post(f"{args.api}/runs", json={"rcept_no": d["rcept_no"]}, timeout=30)
        if r.status_code >= 400:
            print(f"  ✗ {tag} → API {r.status_code}: {r.text[:100]}")
            break
        a = wait_for(d["rcept_no"], args.timeout)
        if not a:
            print(f"  ✗ {tag} → {args.timeout:.0f}초 안에 끝나지 않음")
            break
        print(f"  {tag} → {a['level']} ({a['total_score']}점, {a['status']})")
        if not first_alert and LEVEL_ORDER[a["level"]] >= LEVEL_ORDER["High"]:
            first_alert = (d["filed_at"], a["level"], d["rcept_no"])
        if a["status"] == "WAITING_APPROVAL":
            print("  → PB 승인 대기에서 멈춤. 화면에서 결정한 뒤 이어서 진행하세요.")
            break

    if args.dry_run:
        return
    print()
    if first_alert:
        print(f"첫 High 이상: {first_alert[0]} ({first_alert[1]}, {first_alert[2]})")
        if len(event_date) == 10:
            days = (date.fromisoformat(event_date) - date.fromisoformat(first_alert[0])).days
            print(f"사건일 {event_date} 기준 → 사건보다 {days}일 {'먼저' if days > 0 else '뒤'}")
    else:
        print("High 이상 경보가 나오지 않았습니다.")


if __name__ == "__main__":
    main()
