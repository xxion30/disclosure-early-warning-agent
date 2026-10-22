"""시나리오 A(금양)를 HTTP API로 끝까지 점검한다 (CLAUDE.md 9장 3~13단계).

    # 서버 3개가 떠 있어야 한다: business-mcp(8001), rag-mcp(8002), api(8000, GRAPH_ADAPTER=real)
    uv run python scripts/data/seed_db.py --reset      # 깨끗한 DB에서 시작 (이미 처리한 공시는 409)
    uv run python scripts/demo/check_scenario_a.py

진짜 모델을 부르므로 몇 분 걸리고 비용이 든다(공시 1건에 10~15원 안팎). 연락 업무를 실제로 만든다.
결과는 단계별로 출력만 한다 (통과/실패 판정은 사람이 기대값과 비교).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared.config import get_settings  # noqa: E402
from shared.db import _resolve_url  # noqa: E402

EVENT = "20250321802001"  # 금양 감사보고서제출(감사의견 의견거절)


def login(api: str, email: str, password: str) -> dict:
    r = requests.post(f"{api}/auth/login", json={"email": email, "password": password}, timeout=30)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def wait(api: str, headers: dict, rcept: str, tries: int = 300) -> str:
    for _ in range(tries):
        run = requests.get(f"{api}/runs/{rcept}", headers=headers, timeout=30).json()
        if not run["running"] and run["last_event"] in ("done", "failed", "waiting_approval"):
            return run["last_event"]
        time.sleep(2)
    return "timeout"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--skip-replay", action="store_true", help="사건 전 재생(3단계)을 건너뛴다")
    args = ap.parse_args()
    api = args.api

    pbs = {p["pb_id"]: p for p in json.loads((ROOT / "data" / "customers.json").read_text(encoding="utf-8"))["pbs"]}
    h1 = login(api, pbs["P01"]["email"], pbs["P01"]["password"])
    h2 = login(api, pbs["P02"]["email"], pbs["P02"]["password"])
    db = _resolve_url(get_settings().DB_URL).removeprefix("sqlite:///")
    c = sqlite3.connect(db)
    if c.execute("select count(*) from risk_assessments").fetchone()[0]:
        print("! 이미 평가가 있는 DB입니다. 먼저 `scripts/data/seed_db.py --reset`(과 data/checkpoints.db 삭제)을 하세요.")

    if not args.skip_replay:
        print("== 3. 사건 전 공시 재생 (첫 High에서 멈춤) ==", flush=True)
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo" / "replay_demo.py"), "--stock", "001570",
                              "--until-event", "--timeout", "240", "--api", api], capture_output=True, text=True)
        print("\n".join(l for l in out.stdout.splitlines() if any(k in l for k in ("→", "첫 High", "사건보다", "✗"))), flush=True)

    print("\n== 4~5. 감사의견 거절 공시 ==", flush=True)
    print("POST /runs:", requests.post(f"{api}/runs", json={"rcept_no": EVENT}, headers=h1, timeout=30).status_code)
    print("마지막 이벤트:", wait(api, h1, EVENT), flush=True)
    c = sqlite3.connect(db)
    row = c.execute("select assessment_id,level,total_score,combo_upgraded,status,reasons from risk_assessments where rcept_no=?", (EVENT,)).fetchone()
    if not row:
        raise SystemExit("평가가 만들어지지 않았습니다 (서버 로그와 audit_logs를 확인하세요)")
    aid = row[0]
    print("평가:", row[:5], "(기대: Critical, WAITING_APPROVAL)")
    for b in json.loads(row[5]):
        print("  -", b["group"], b["item"], "| 점수", b["points"], "| 반영률", b.get("weight"),
              "| 과거" if b.get("from_history") else "| 새 공시", "| 근거", len(b.get("evidence", [])))

    detail = requests.get(f"{api}/alerts/{aid}", headers=h1, timeout=30).json()
    cs = detail.get("affected_customers") or detail.get("customers") or []
    pr = lambda p: sorted(x["customer_id"] for x in cs if x.get("priority") == p)  # noqa: E731
    print(f"\n== 6. 영향 고객 {len(cs)}명 (기대 7) | P1 {pr('P1')} | P2 {pr('P2')}")
    drafts = requests.get(f"{api}/alerts/{aid}/drafts", headers=h1, timeout=30).json()
    n = len(drafts.get("drafts", drafts if isinstance(drafts, list) else []))
    print(f"== 7. P01 담당 안내문 초안 {n}건 | 가드레일 flags: {detail.get('guardrail_flags')}")

    owner = dict(c.execute("select customer_id,pb_id from customers").fetchall())
    targets = pr("P1") + pr("P2")
    p01 = sorted(x for x in targets if owner[x] == "P01")
    p02 = sorted(x for x in targets if owner[x] == "P02")

    print("\n== 9~10. P01 승인:", p01, flush=True)
    dec = [{"customer_id": x, "action": "approve"} for x in p01]
    dec[0]["edited_draft"] = "{고객명}님, 금양 감사의견 관련 공시가 접수되어 안내드립니다. 상담이 필요하시면 연락 주세요."
    print("decision:", requests.post(f"{api}/alerts/{aid}/decision", json={"decisions": dec}, headers=h1, timeout=60).status_code)
    time.sleep(8)
    print("연락 업무:", c.execute("select customer_id,status from contact_tasks order by customer_id").fetchall(), f"(기대 {p01})")
    forbidden = requests.post(f"{api}/alerts/{aid}/decision", json={"decisions": [{"customer_id": p02[0], "action": "approve"}]}, headers=h1, timeout=30)
    print("P01이 P02 고객 승인 시도:", forbidden.status_code, "(기대 403)")

    print("\n== 11~12. P02 결정:", p02, flush=True)
    dec = [{"customer_id": x, "action": "reject", "reject_reason": "고객 요청으로 연락 보류"} if x == "C002"
           else {"customer_id": x, "action": "approve"} for x in p02]
    print("decision:", requests.post(f"{api}/alerts/{aid}/decision", json={"decisions": dec}, headers=h2, timeout=60).status_code)
    time.sleep(8)
    c = sqlite3.connect(db)
    print("연락 업무:", c.execute("select customer_id,pb_id,priority,status from contact_tasks order by customer_id").fetchall())
    print("평가 상태:", c.execute("select status from risk_assessments where assessment_id=?", (aid,)).fetchone()[0], "(기대 DONE)")
    print("audit_logs:", dict(c.execute("select action,count(*) from audit_logs group by action").fetchall()))
    reject = c.execute("select payload from audit_logs where action='decision.reject'").fetchall()
    print("C002 반려 사유 기록:", any("연락 보류" in json.loads(p[0]).get("reject_reason", "") for p in reject))
    print("\nLangSmith(LANGSMITH_API_KEY가 있으면)에서 프로젝트", get_settings().LANGSMITH_PROJECT, "의 trace를 확인한다.")


if __name__ == "__main__":
    main()
