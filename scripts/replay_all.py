"""데모 회사를 차례로 재생하고, 사건 공시까지 처리한 뒤 기대 결과(data/demo_companies.json)와 비교한다.

    # 서버가 떠 있어야 한다: uv run python scripts/run_stack.py  (진짜 모드)
    uv run python scripts/replay_all.py                 # 금양을 뺀 4개 회사 (위니아·오스템·CGV·SK)
    uv run python scripts/replay_all.py --stock 048260  # 한 회사만
    uv run python scripts/replay_all.py --all           # 금양까지 5개 전부

회사마다: ① 사건 전 공시를 날짜순으로 흘려보낸다(첫 High 이상이 나오면 멈춤. --keep-going이면 계속)
② 사건 공시를 넣는다 ③ 기대 결과와 비교해 ✓/✗로 보여 준다. 결과표는 eval/results/scenario_b.md에도 저장한다.
처리한 공시는 화면(경보 목록)에 그대로 나타난다. 이미 처리한 공시는 건너뛴다(409).
진짜 모델을 부르므로 시간이 걸리고(회사당 몇 분) 비용이 든다(공시 1건에 10~15원 안팎).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import replay_demo as rd  # noqa: E402  (login, wait_for, load 재사용)

ORDER = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
KST = timezone(timedelta(hours=9))


def post_run(api: str, headers: dict, rcept_no: str) -> int:
    return requests.post(f"{api}/runs", json={"rcept_no": rcept_no}, headers=headers, timeout=30).status_code


def process(api: str, headers: dict, d: dict, timeout: float, tag_prefix: str = "") -> dict | None:
    """공시 1건 처리. 평가 요약(dict) 또는 신호 없음({'status': 'NO_SIGNAL'}), 실패면 None."""
    tag = f"{d['filed_at']} {d['rcept_no']} {d['title'][:28]}"
    code = post_run(api, headers, d["rcept_no"])
    if code == 409:
        a = rd.assessment_of(d["rcept_no"])
        print(f"  - {tag_prefix}{tag} → 이미 처리됨" + (f" ({a['level']} {a['total_score']}점, {a['status']})" if a else ""), flush=True)
        return {**a, "skipped": True} if a else {"status": "NO_SIGNAL", "level": "-", "total_score": 0, "skipped": True}
    if code >= 400:
        print(f"  ✗ {tag_prefix}{tag} → API {code}", flush=True)
        return None
    a = rd.wait_for(api, headers, d["rcept_no"], timeout)
    if not a:
        print(f"  ✗ {tag_prefix}{tag} → {timeout:.0f}초 안에 끝나지 않음", flush=True)
        return None
    if a["status"] != "NO_SIGNAL":
        print(f"  {tag_prefix}{tag} → {a['level']} ({a['total_score']}점, {a['status']})", flush=True)
    return a


def affected(api: str, headers: dict, rcept_no: str) -> dict:
    """사건 공시의 평가에서 영향 고객 {P1: [...], P2: [...]}."""
    from sqlalchemy import select

    from shared.db import get_session
    from shared.models import RiskAssessment

    with get_session() as s:
        aid = s.scalars(select(RiskAssessment.assessment_id).where(RiskAssessment.rcept_no == rcept_no)).first()
    if aid is None:
        return {}
    detail = requests.get(f"{api}/alerts/{aid}", headers=headers, timeout=30).json()
    out: dict[str, list[str]] = {}
    for c in detail.get("customers") or []:
        out.setdefault(c["priority"], []).append(c["customer_id"])
    return {k: sorted(v) for k, v in out.items()} | {"assessment_id": aid}


def run_company(api: str, headers: dict, stock: str, timeout: float, keep_going: bool) -> dict:
    company, disclosures = rd.load(stock)
    event = company.get("event") or {}
    event_date = (event.get("date") or "")[:10]
    event_rcept = event.get("rcept_no")
    window = [d for d in disclosures if company["replay_window"]["from"] <= d["filed_at"] <= company["replay_window"]["to"]]
    before = [d for d in window if not event_date or d["filed_at"] < event_date]
    res = {"company": company, "assessments": [], "first_alert": None, "event": None, "customers": {}, "errors": 0}

    print(f"\n=== {company['name']} ({stock}) — 사건 전 공시 {len(before)}건 재생 ===", flush=True)
    for d in before:
        a = process(api, headers, d, timeout)
        if a is None:
            res["errors"] += 1
            break
        if a["status"] == "NO_SIGNAL":
            continue
        res["assessments"].append({**a, "filed_at": d["filed_at"], "rcept_no": d["rcept_no"]})
        if a["status"] in ("FAILED", "MANUAL_CHECK"):
            res["errors"] += 1
        if res["first_alert"] is None and ORDER.get(a["level"], 0) >= ORDER["High"]:
            res["first_alert"] = {"date": d["filed_at"], "level": a["level"], "rcept_no": d["rcept_no"], "score": a["total_score"]}
            if not keep_going:
                print("  → 첫 High 이상. 승인 대기에서 멈춥니다(--keep-going이면 계속).", flush=True)
                break

    if event_rcept:
        ev_d = next((d for d in disclosures if d["rcept_no"] == event_rcept), None)
        print(f"--- 사건 공시: {event.get('type')} ({event_date}) ---", flush=True)
        if ev_d:
            a = process(api, headers, ev_d, timeout, "사건 ")
            res["event"] = a
            if a and a["status"] != "NO_SIGNAL":
                res["assessments"].append({**a, "filed_at": ev_d["filed_at"], "rcept_no": event_rcept})
                res["customers"] = affected(api, headers, event_rcept)
        else:
            print("  ✗ 사건 접수번호가 공시 목록에 없습니다", flush=True)
    elif not before:
        pass
    return res


def judge(res: dict) -> list[tuple[bool | None, str]]:
    """기대 결과와 비교한 (통과 여부, 설명) 목록."""
    c, exp = res["company"], res["company"].get("expected") or {}
    out: list[tuple[bool | None, str]] = []
    event_date = ((c.get("event") or {}).get("date") or "")[:10]
    fa = res["first_alert"]
    if "first_alert_before_event" in exp:
        want = exp["first_alert_before_event"]
        got = bool(fa and event_date and fa["date"] < event_date)
        extra = ""
        if fa and event_date:
            extra = f" ({fa['date']} {fa['level']}, 사건보다 {(date.fromisoformat(event_date) - date.fromisoformat(fa['date'])).days}일 먼저)"
        out.append((got == want, f"사건 전 경보 {'있음' if want else '없음'} 기대 → {'있음' if got else '없음'}{extra}"))
    if exp.get("first_alert_level_min") and fa:
        out.append((ORDER[fa["level"]] >= ORDER[exp["first_alert_level_min"]], f"첫 경보 등급 {exp['first_alert_level_min']} 이상 기대 → {fa['level']}"))
    if exp.get("event_level"):
        got = (res["event"] or {}).get("level")
        out.append((got == exp["event_level"], f"사건 공시 등급 {exp['event_level']} 기대 → {got}"))
    holders = exp.get("holders") or (sorted(exp.get("p1", []) + exp.get("p2", [])) if exp.get("p1") else None)
    if holders:
        got = sorted(set(res["customers"].get("P1", []) + res["customers"].get("P2", [])))
        out.append((got == sorted(holders), f"영향 고객 {sorted(holders)} 기대 → {got}"))
    if exp.get("p1") and exp.get("p2"):
        out.append((res["customers"].get("P1") == sorted(exp["p1"]) and res["customers"].get("P2") == sorted(exp["p2"]),
                    f"P1 {sorted(exp['p1'])} / P2 {sorted(exp['p2'])} 기대 → P1 {res['customers'].get('P1')} / P2 {res['customers'].get('P2')}"))
    if exp.get("max_level"):
        top = max((a["level"] for a in res["assessments"]), key=lambda lv: ORDER.get(lv, 0), default="Low")
        out.append((ORDER[top] <= ORDER[exp["max_level"]], f"최고 등급 {exp['max_level']} 이하 기대 → {top}"))
    if exp.get("alert") is False:
        got = sum(1 for a in res["assessments"] if ORDER.get(a["level"], 0) >= ORDER["High"])
        out.append((got == 0, f"경보(High 이상) 없음 기대 → {got}건"))
    if "base_score" in exp:
        from sqlalchemy import select

        from shared.db import get_session
        from shared.models import Company

        with get_session() as s:
            got = s.scalars(select(Company.base_score).where(Company.stock_code == c["stock_code"])).first()
        out.append((got == exp["base_score"], f"기본 점수 {exp['base_score']} 기대 → {got}"))
    if res["errors"]:
        out.append((False, f"실행 오류·수동 확인 {res['errors']}건 (audit_logs의 agent_failure 확인)"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--stock", action="append", help="종목코드 (여러 번 가능)")
    ap.add_argument("--all", action="store_true", help="금양 포함 5개 전부")
    ap.add_argument("--keep-going", action="store_true", help="첫 High 이상 뒤에도 사건 전 공시를 계속 재생")
    ap.add_argument("--timeout", type=float, default=240, help="공시 1건당 대기 초")
    ap.add_argument("--out", default=str(ROOT / "eval" / "results" / "scenario_b.md"), help="결과표 저장 경로")
    args = ap.parse_args()

    companies = json.loads((ROOT / "data" / "demo_companies.json").read_text(encoding="utf-8"))["companies"]
    stocks = args.stock or [c["stock_code"] for c in companies if args.all or c["stock_code"] != "001570"]
    headers = rd.login(args.api, None, None)

    results = [run_company(args.api, headers, s, args.timeout, args.keep_going) for s in stocks]

    lines = ["# 시나리오 B — 데모 회사 재생 결과 (기대 결과 대비)", "",
             f"실행: {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} · 진짜 모델·진짜 MCP 도구 · 실제 DART 공시, 가상 고객", ""]
    print("\n\n========== 결과 요약 ==========")
    for r in results:
        name = r["company"]["name"]
        print(f"\n[{name}]")
        lines += [f"## {name} ({r['company']['stock_code']})", ""]
        for ok, text in judge(r):
            mark = "✓" if ok else "✗"
            print(f"  {mark} {text}")
            lines.append(f"- {mark} {text}")
        ev = r["assessments"]
        if ev:
            lines += ["", "| 공시일 | 등급 | 점수 | 상태 |", "| --- | --- | --- | --- |"]
            lines += [f"| {a['filed_at']} | {a['level']} | {a['total_score']} | {a['status']} |" for a in ev]
        lines.append("")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
