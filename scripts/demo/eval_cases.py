"""테스트 사례(R3·R5)를 규칙 엔진만으로 채점한다. AI·모델 비용 없음, DART 호출 없음.

    uv run python scripts/demo/eval_cases.py            # 결과표를 화면에 출력
    uv run python scripts/demo/eval_cases.py --write    # docs/verification.md의 결과 칸도 바꿈

무엇을 하나
- 사례에 적힌 실제 공시(제목·접수일)와 실제 값(facts)을 risk_engine.stages.calculate에 넣어 단계를 계산한다.
- 공시 종류는 rules.yaml의 phase1 제목 분류를 그대로 쓴다 (R4 어댑터와 같은 규칙).
- 판정에 필요한 값은 사례의 facts에서만 읽는다. 없으면 비워 두고(규칙 엔진은 기본 점수만 셈) '빠진 값'으로 센다.

규칙만으로 정할 수 없는 것 (AI가 원문을 읽고 정하는 몫)
- 거래정지 해제·기간 변경·불성실공시 당일 정지·풍문, 횡령 진행사항, 상장폐지 이의신청 같은 후속 공시는
  R1 결정(PR #31)상 경보에서 빠지지만, 그 구분은 원문에서 뽑는 값(lifted·resolved)에 달려 있다.
  이런 제목은 채점에서 빼고 'AI 판단 필요'로 따로 보여 준다. 정정 공시는 같은 사건이라 새 신호로 넣지 않는다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from risk_engine.loader import load_rules  # noqa: E402
from risk_engine.stages import ALERT_STAGES, calculate  # noqa: E402
from shared.rule_catalog import load_catalog  # noqa: E402

CASE_FILES = [ROOT / "eval" / "cases" / "r3_cases.json", ROOT / "eval" / "cases" / "r5_cases.json"]
VERIFICATION = ROOT / "docs" / "verification.md"
START, END = "<!-- r5-eval:start -->", "<!-- r5-eval:end -->"

STAGE_KO = {"RED": "🔴 즉시 경보", "ORANGE": "🟠 사건 경보", "YELLOW": "🟡 재무 경보", "COMPOSITE": "복합 위험", "WATCH": "관찰 중", "NONE": "경보 없음"}
EVENT_STAGES = ("RED", "ORANGE")  # R1 3-4: 사건 계열, 같은 날이면 RED 우선
# R1 결정(PR #31)으로 경보에서 빠지는 후속·해제 공시. 구분은 AI가 원문으로 하므로 규칙만 채점에서 뺀다
AI_ONLY = re.compile(r"해제|기간변경|불성실공시|중요내용공시|풍문|조회공시|진행사항|이의신청|개선기간|투자유의|결과보고서|사실확인")
CORRECTION = re.compile(r"^\[[^\]]*정정\]")
SKIP_ROLES = {"correction", "follow_up", "resolution"}


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


class Engine:
    def __init__(self):
        rules = load_rules()
        catalog = load_catalog()
        self.entries, self.allowed = catalog["classification"], catalog["allowed"]
        self.red, self.orange, self.yellow = set(rules["red"]), set(rules["orange"]), set(rules["yellow"])

    def classify(self, title: str) -> str | None:
        t = "".join((title or "").split())
        if t.startswith("[첨부추가]"):
            return None
        return next((e["type"] for e in self.entries if any(w in t for w in e["keywords"])
                     or (e.get("all_keywords") and all(w in t for w in e["all_keywords"]))), None)

    def skip_reason(self, step: dict) -> str | None:
        title = "".join((step.get("title") or "").split())
        if step.get("role") in SKIP_ROLES:
            return {"correction": "정정(같은 사건)", "follow_up": "후속 공시", "resolution": "해소 공시"}[step["role"]] + " — AI 판단 필요"
        if CORRECTION.match(title):
            return "정정(같은 사건)"
        if AI_ONLY.search(title):
            return "후속·해제 공시 — AI 판단 필요"
        return None

    def signals(self, step: dict) -> tuple[list[dict], list[str]]:
        """공시 한 건 → 규칙 엔진 입력. 두 번째 값은 빠진 값 이름 목록"""
        dtype = step.get("disclosure_type") or self.classify(step.get("title"))
        if not dtype or not self.allowed.get(dtype):
            return [], []
        rcept, filed, title = step["rcept_no"], step["filed_at"], step.get("title") or ""
        base = {"rcept_no": rcept, "event_id": rcept, "filed_at": filed}
        facts = step.get("facts") or {}
        if dtype in self.red:
            return [{**base, "signal_type": dtype, "detail": {"title": title}}], []
        if dtype in ("AUDIT_REPORT", "PERIODIC_REPORT"):
            # 재무 지표(R3 수집기 값). 정상 운영에서는 financial_snapshots로 들어오는 값이다
            out = []
            for st, v in facts.items():
                if st in self.yellow and isinstance(v, dict) and v.get("status") == "OK":
                    detail = {k: x for k, x in v.items() if k not in ("status", "notes")}
                    out.append({**base, "event_id": f"{rcept}:{st}", "signal_type": st, "detail": {**detail, "title": title}})
            return out, []
        st = self.allowed[dtype][0]
        if st not in self.orange:
            return [], []
        detail, evidence, missing = {"title": title}, [], []
        api = (step.get("facts_meta") or {}).get("api") or "항목 API"
        if st == "RIGHTS_OFFERING" and facts:
            new, before = num(facts.get("nstk_ostk_cnt")), num(facts.get("bfic_tisstk_ostk"))
            if new is not None and before:
                detail["ratio_pct"] = new / before * 100
            if facts.get("ic_mthn"):
                detail["third_party"] = "제3자" in facts["ic_mthn"]
                if detail["third_party"]:
                    evidence.append({"source": f"DART {api}", "quote": f"증자방식: {facts['ic_mthn']}", "rcept_no": rcept, "location": f"{api}.ic_mthn"})
        spec = load_rules()["orange"][st]
        for key in [*spec.get("tiers", {}), *spec.get("flags", {})]:
            if key not in detail:
                missing.append(f"{st}.{key}")
        return [{**base, "signal_type": st, "detail": detail, "evidence": evidence}], missing


def stage_of(engine: Engine, signals: list[dict], as_of: str) -> dict:
    if not signals:
        return {"stage": "NONE", "active_stages": ["NONE"], "manual_check": False}
    return calculate(new_signals=[s for s in signals if s["filed_at"] <= as_of], as_of=as_of)


def replay(engine: Engine, steps: list[dict], as_of: str) -> dict:
    """공시를 날짜순으로 넣으며 날짜마다 단계를 계산한다 → 단계 변화·첫 경보일·경보 횟수"""
    sigs, missing, skipped, path, alerts = [], [], [], [], 0
    prev_active: set = set()
    by_date: dict[str, list] = {}
    for s in sorted(steps, key=lambda x: (x["filed_at"], x["rcept_no"])):
        if s["filed_at"] > as_of:
            continue
        reason = engine.skip_reason(s)
        if reason:
            skipped.append({**{k: s.get(k) for k in ("filed_at", "rcept_no", "title")}, "reason": reason})
            by_date.setdefault(s["filed_at"], [])
            continue
        new, miss = engine.signals(s)
        sigs.extend(new)
        missing.extend(miss)
        by_date.setdefault(s["filed_at"], []).append(s)
    first_alert = first_alert_stage = None
    first_event_alert = first_event_alert_stage = None
    for d in sorted(by_date):
        if d > as_of:
            break
        r = stage_of(engine, sigs, d)
        active = set(r["active_stages"]) & set(ALERT_STAGES)
        if active - prev_active:
            alerts += 1  # 새 경보: 경보 단계가 새로 생기거나 다른 종류의 경보 단계가 더해짐
            if first_alert is None:
                first_alert, first_alert_stage = d, r["stage"]
        if first_event_alert is None and active.intersection(EVENT_STAGES):
            first_event_alert = d
            first_event_alert_stage = next(st for st in EVENT_STAGES if st in active)
        prev_active = active
        path.append((d, r["stage"]))
    final = stage_of(engine, sigs, as_of)
    return {"final": final["stage"], "active": final["active_stages"], "path": path, "first_alert": first_alert,
            "first_alert_stage": first_alert_stage, "alerts": alerts,
            "first_alert_lead_days": days_before(first_alert, as_of) if first_alert else None,
            "first_event_alert": first_event_alert, "first_event_alert_stage": first_event_alert_stage,
            "first_event_alert_lead_days": days_before(first_event_alert, as_of) if first_event_alert else None,
            "missing": sorted(set(missing)), "skipped": skipped, "manual": final.get("manual_check")}


def single_steps(case: dict) -> list[dict]:
    """R3 단건 사례 → 판정에 쓰는 공시(trigger) + 사례 facts"""
    out = []
    for e in case.get("evidence", []):
        if e.get("role") in ("trigger", None):
            out.append({"rcept_no": e["rcept_no"], "filed_at": e["filed_at"], "title": e["title"],
                        "facts": case.get("facts"), "facts_meta": case.get("facts_meta"), "role": "trigger"})
    return out


def first_alert_matches(result: dict, expected: dict | None, *, event: bool = False) -> bool | None:
    """저장 데이터의 계산 회귀만 채점한다. status는 별도로 관리하는 원본 검증 상태다."""
    if expected is None:
        return None
    prefix = "first_event_alert" if event else "first_alert"
    return (result[prefix] == expected["date"] and result[f"{prefix}_stage"] == expected["stage"]
            and result[f"{prefix}_lead_days"] == expected["lead_days"])


def evaluate(cases: list[dict]) -> dict:
    eng = Engine()
    scored, undecided, ai_needed, timelines = [], [], [], []
    for c in cases:
        scope, group = c.get("scope"), c.get("group")
        if scope == "single_filing":
            steps = single_steps(c)
            sigs, missing = [], []
            for s in steps:
                new, miss = eng.signals(s)
                sigs += new
                missing += miss
            got = stage_of(eng, sigs, c["as_of"])["stage"]
            row = {"case": c, "got": got, "missing": sorted(set(missing))}
            if group in ("NOT_RED", "RESOLUTION"):
                ai_needed.append(row)  # 해제·후속·정정·해소: 원문 판단(AI) 몫
            elif c.get("expected_stage") is None:
                undecided.append(row)
            else:
                scored.append({**row, "ok": got == c["expected_stage"]})
        elif scope in ("timeline", "company_year"):
            if scope == "company_year" and "steps" not in c:
                # R3 정상 사례: 재무 지표 + 12개월 🟠 후보 제목(접수번호 없음 → 기준일로 넣음)
                steps = [{"rcept_no": c["evidence"][0]["rcept_no"], "filed_at": c["as_of"], "title": c["evidence"][0]["title"],
                          "disclosure_type": "PERIODIC_REPORT", "facts": c.get("facts")}]
                steps += [{"rcept_no": f"title-{i}", "filed_at": c["as_of"], "title": t} for i, t in enumerate(c.get("window_orange_candidate_titles", []))]
            else:
                steps = c.get("steps", [])
            window = c.get("window") or {}
            end = min(c["as_of"], window.get("to", c["as_of"]))
            steps = [s for s in steps if s["filed_at"] <= end
                     and (not window.get("from") or s["filed_at"] >= window["from"])]
            r = replay(eng, steps, c["as_of"])
            exp_alert = c.get("expected_alert")
            exp_final = c.get("expected_final_stage") or (c.get("expected_stage") if scope == "timeline" else None)
            step_checks = []
            if scope == "timeline" and any("expected_stage_after" in s for s in steps):
                # R3 타임라인: 단계별 기대값과 비교 (그날까지 넣은 결과)
                for s in steps:
                    if not s.get("expected_stage_after"):
                        continue
                    if eng.skip_reason(s):
                        step_checks.append({"step": s, "got": None, "ok": None, "ai": eng.skip_reason(s)})
                        continue
                    upto = [x for x in steps if (x["filed_at"], x["rcept_no"]) <= (s["filed_at"], s["rcept_no"])]
                    got = replay(eng, upto, s["filed_at"])["final"]
                    step_checks.append({"step": s, "got": got, "ok": got == s["expected_stage_after"]})
            # 계산 회귀와 당시 원본 값 검증은 별개다. pending_data_check도 계산은 검사한다.
            exp_stages = c.get("expected_final_stages") or ([exp_final] if exp_final else [])
            checks = []
            if exp_stages:
                checks.append(all(st in r["active"] for st in exp_stages))
                if c.get("expected_final_stages") and exp_final:
                    checks.append(r["final"] == exp_final)
            r["first_alert_check"] = first_alert_matches(r, c.get("expected_first_alert"))
            r["first_event_alert_check"] = first_alert_matches(r, c.get("expected_first_event_alert"), event=True)
            checks.extend(r[key] for key in ("first_alert_check", "first_event_alert_check") if r[key] is not None)
            r["exp_ok"] = all(checks) if checks else None
            r["first_alert_status"] = (c.get("expected_first_alert") or {}).get("status")
            r["first_event_alert_status"] = (c.get("expected_first_event_alert") or {}).get("status")
            r["fa_category"] = (c.get("false_alarm_policy") or {}).get("category")
            timelines.append({"case": c, **r, "exp_alert": exp_alert, "exp_final": exp_final, "exp_stages": exp_stages,
                              "step_checks": step_checks})
    return {"scored": scored, "undecided": undecided, "ai_needed": ai_needed, "timelines": timelines}


def days_before(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def summarize(res: dict) -> dict:
    """채점 결과 → 지표 숫자 (기준값 비교표용, scripts/demo/compare_thresholds.py)"""
    sc, tl = res["scored"], res["timelines"]
    alert_cases = [t for t in tl if t["exp_alert"] is True or t["exp_final"] in ALERT_STAGES]
    normal_cases = [t for t in tl if t["exp_alert"] is False]
    false_alarms = [t for t in normal_cases if t["final"] in ALERT_STAGES or t["alerts"]]
    exp_cases = [t for t in tl if t["exp_ok"] is not None]
    weekly = [t["alerts"] / max(1, days_before(t["case"]["window"]["from"], t["case"]["window"]["to"]) / 7) for t in tl if t["case"].get("window")]
    by_id = {t["case"]["case_id"]: t for t in tl}

    def lead(cid, *, event=False):
        t = by_id.get(cid)
        if t and not has_event_reference(t):
            return None  # R3 타임라인의 as_of는 사건일이 아니라 기간 끝이다.
        return t["first_event_alert_lead_days" if event else "first_alert_lead_days"] if t else None

    return {
        "single": (sum(r["ok"] for r in sc), len(sc)),
        "expected": (sum(t["exp_ok"] for t in exp_cases), len(exp_cases)),
        "hits": (sum(t["final"] in ALERT_STAGES for t in alert_cases), len(alert_cases)),
        "false_alarms": (len(false_alarms), len(normal_cases)),
        "false_alarm_ids": [t["case"]["case_id"] for t in false_alarms],
        "false_alarms_pb": sum(1 for t in false_alarms if t.get("fa_category") == "PB 판단 영역"),
        "weekly": sum(weekly) / len(weekly) if weekly else 0.0,
        "lead_days": {cid: lead(cid) for cid in by_id},
        "event_lead_days": {cid: lead(cid, event=True) for cid in by_id},
        "first_alert_stage": {cid: t["first_alert_stage"] for cid, t in by_id.items()},
        "first_event_alert_stage": {cid: t["first_event_alert_stage"] for cid, t in by_id.items()},
        "first_alert_status": {cid: t["first_alert_status"] for cid, t in by_id.items()},
        "first_alert_note": {cid: first_alert_note(t) for cid, t in by_id.items()},
        "final": {cid: t["final"] for cid, t in by_id.items()},
        "active": {cid: t["active"] for cid, t in by_id.items()},
    }


def has_event_reference(result: dict) -> bool:
    case = result["case"]
    return not (case["case_id"].startswith("r3-") and case["scope"] == "timeline")


def first_alert_note(result: dict, *, event: bool = False) -> str:
    prefix = "first_event_alert" if event else "first_alert"
    expected = result["case"].get(f"expected_{prefix}")
    if expected is None:
        return ""
    if not first_alert_matches(result, expected, event=event):
        return "기대값과 다름"
    return "R3 검증 대기" if expected.get("status") == "pending_data_check" else ""


def describe_first_alert(result: dict, *, event: bool = False) -> str:
    prefix = "first_event_alert" if event else "first_alert"
    if result[prefix] is None:
        text = "-"
    else:
        text = f"{result[prefix]} · {STAGE_KO[result[f'{prefix}_stage']]}"
        if has_event_reference(result):
            text += f" (기준일 {result[f'{prefix}_lead_days']}일 전)"
    note = first_alert_note(result, event=event)
    if note:
        text += f" · {note}"
    return text


def render(res: dict) -> str:
    L = []
    sc = res["scored"]
    ok = sum(r["ok"] for r in sc)
    tl = res["timelines"]
    alert_cases = [t for t in tl if t["exp_alert"] is True or t["exp_final"] in ALERT_STAGES]
    normal_cases = [t for t in tl if t["exp_alert"] is False]
    false_alarms = [t for t in normal_cases if t["final"] in ALERT_STAGES or t["alerts"]]
    hits = [t for t in alert_cases if t["final"] in ALERT_STAGES]
    steps_total = sum(1 for t in tl for c in t["step_checks"] if c["ok"] is not None)
    steps_ok = sum(1 for t in tl for c in t["step_checks"] if c["ok"])
    steps_ai = sum(1 for t in tl for c in t["step_checks"] if c["ok"] is None)
    exp_cases = [t for t in tl if t["exp_ok"] is not None]
    L.append(f"_생성: `uv run python scripts/demo/eval_cases.py --write` · 사례 {len(sc) + len(res['undecided']) + len(res['ai_needed']) + len(tl)}건 (R3·R5) · 규칙 엔진만 (AI 추출 없음)_")
    L.append("")
    lead_case = next((t for t in tl if t["case"]["case_id"] == "r5-timeline-001"), None)
    if lead_case:
        if lead_case["first_event_alert"]:
            days = lead_case["first_event_alert_lead_days"]
            L.append(f"**핵심: 감사의견 거절 공시보다 {days}일 앞서 사건 경보가 발생했다.** — 금양의 최초 사건 경보(🔴·🟠 대상): {describe_first_alert(lead_case, event=True)}.")
        L.append(f"**최초 전체 경보(저장 데이터 재생):** {describe_first_alert(lead_case)}.")
        L.append("평가 기간 안에서 계산한 과거 데이터 재생 결과이며 실제 운영에서 당시 알림을 보냈다는 뜻이 아니다. 계산 검사 통과는 당시 원본 데이터 검증 완료를 뜻하지 않는다.")
        if lead_case["first_alert_status"] == "pending_data_check":
            filings = lead_case["case"].get("data_check_filings", [])
            targets = " · ".join(f"{f['date']} {f['role']}({f['rcept_no']})" for f in filings)
            L.append("**기대값의 원본 검증 보류 (`pending_data_check`):** "
                     + (f"확인 대상: {targets}. " if targets else "")
                     + "R3가 재무 값·감사의견을 확인하기 전까지 이 기대값의 선행 기간은 확정 성과로 쓰지 않는다.")
        L.append("")
    fa_pb = sum(1 for t in false_alarms if t.get("fa_category") == "PB 판단 영역")
    fa_note = f" — {fa_pb}건은 PB 판단 영역(허용한 비용)" if fa_pb else ""
    L.append("평가는 **적중을 먼저** 보고, 오탐은 허용한 비용으로 원인과 함께 설명한다 (R1 위험 정책 원칙 3).")
    L.append("")
    L.append("| 지표 | 결과 | 설명 |")
    L.append("| --- | --- | --- |")
    L.append(f"| **적중 (1순위)** | **{len(hits)} / {len(alert_cases)}** | 경보가 있어야 하는 기간 사례에서 기준일에 경보 단계가 나온 수 |")
    L.append(f"| 오탐 | **{len(false_alarms)} / {len(normal_cases)}**{fa_note} | 경보가 없어야 하는 기간 사례(정상 회사)에서 경보가 울린 수 |")
    L.append(f"| 단건 사례 정답률 | **{ok} / {len(sc)}** | 정답이 정해진 공시 한 건 사례에서 계산한 단계가 정답과 같은 수 |")
    L.append(f"| 타임라인 단계별 정답률 | **{steps_ok} / {steps_total}** | R3 타임라인의 단계마다 기대 단계와 같은 수. AI 판단이 필요한 단계 {steps_ai}개는 뺌 |")
    L.append(f"| 기간 사례 계산 정답 | **{sum(t['exp_ok'] for t in exp_cases)} / {len(exp_cases)}** | 기준일 단계와 지정된 최초 전체·사건 경보 종류/날짜/선행 기간의 계산 일치. 원본 데이터 검증과 별개 |")
    weekly = [t["alerts"] / max(1, days_before(t["case"]["window"]["from"], t["case"]["window"]["to"]) / 7) for t in tl if t["case"].get("window")]
    if weekly:
        L.append(f"| 경보 빈도 | 종목당 주 **{sum(weekly) / len(weekly):.2f}회** (최대 {max(weekly):.2f}) | 12개월 기간 사례에서 새 경보(경보 단계가 새로 생김) 수 ÷ 주 수 |")
    L.append(f"| 정답 미정 | {len(res['undecided'])}건 | 정답(expected_stage)이 비어 있어 채점하지 않음. 아래에 계산값을 적어 R1·R3가 정할 때 참고 |")
    L.append(f"| AI 판단 필요 | {len(res['ai_needed'])}건 | 해제·후속·정정·해소 공시. 제목만으로는 구분할 수 없어 채점하지 않음 |")
    L.append("")
    wrong = [r for r in sc if not r["ok"]]
    if wrong:
        L.append("**틀린 단건 사례**")
        L.append("")
        L.append("| 사례 | 정답 | 계산 | 빠진 값 |")
        L.append("| --- | --- | --- | --- |")
        for r in wrong:
            c = r["case"]
            L.append(f"| {c['case_id']} {c['title']} | {STAGE_KO[c['expected_stage']]} | {STAGE_KO[r['got']]} | {', '.join(r['missing']) or '-'} |")
        L.append("")
    L.append("**기간 사례 (타임라인·정상 회사)**")
    L.append("")
    L.append("R3 타임라인의 기간 끝은 사건 공시일로 확정되지 않았으므로 최초 경보의 날짜·종류만 표시하고 선행 일수는 생략한다.")
    L.append("")
    L.append("| 사례 | 기간 | 기준일 기대 | 기준일에 켜진 단계 | 최초 전체 경보(재생) | 최초 사건 경보(🔴·🟠) | 새 경보 수 | 빠진 값·제외한 공시 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for t in tl:
        c = t["case"]
        w = c.get("window") or {}
        if c.get("expected_final_stages"):
            exp = " · ".join(STAGE_KO[x] for x in t["exp_stages"]) + f" (맨 위 {STAGE_KO[t['exp_final']]})"
        else:
            exp = STAGE_KO.get(t["exp_final"]) if t["exp_final"] else ("경보 없음(NONE·WATCH)" if t["exp_alert"] is False else "미정")
        first = describe_first_alert(t)
        first_event = describe_first_alert(t, event=True)
        note = f"빠진 값 {len(t['missing'])}종, 제외 {len(t['skipped'])}건"
        active = " + ".join(STAGE_KO[a] for a in t["active"])
        mark = "" if t["exp_ok"] is None else (" ✓" if t["exp_ok"] else " ✗")
        if t["exp_alert"] is False:
            mark = (f" ✗ 오탐 · {t['fa_category']}" if t.get("fa_category") else " ✗ 오탐") if (t["final"] in ALERT_STAGES or t["alerts"]) else " ✓"
        L.append(f"| {c['case_id']} {c['corp_name']} | {w.get('from', '-')} ~ {w.get('to', c['as_of'])} | {exp} | {active}{mark} | {first} | {first_event} | {t['alerts']} | {note} |")
    L.append("")
    step_wrong = [(t, x) for t in tl for x in t["step_checks"] if x["ok"] is False]
    if step_wrong:
        L.append("**틀린 타임라인 단계**")
        L.append("")
        for t, x in step_wrong:
            s = x["step"]
            L.append(f"- {t['case']['case_id']} {s['filed_at']} {s['title']}: 기대 {STAGE_KO[s['expected_stage_after']]}, 계산 {STAGE_KO[x['got']]}")
        L.append("")
    if res["undecided"]:
        L.append("**정답 미정 사례의 계산값 (참고)**")
        L.append("")
        L.append("| 사례 | 계산 | 빠진 값 | 미정 이유 |")
        L.append("| --- | --- | --- | --- |")
        for r in res["undecided"]:
            c = r["case"]
            why = (c.get("ambiguity") or c.get("expectation_source") or "").replace("|", "/")[:90]
            L.append(f"| {c['case_id']} {c['title']} | {STAGE_KO[r['got']]} | {', '.join(r['missing']) or '-'} | {why} |")
        L.append("")
    if res["ai_needed"]:
        L.append("**AI 판단이 필요한 사례 (제목만으로 계산한 값, 채점 안 함)**")
        L.append("")
        cnt = Counter((r["case"].get("expected_stage") or "미정", r["got"]) for r in res["ai_needed"])
        for (exp, got), n in sorted(cnt.items()):
            L.append(f"- 정답 {STAGE_KO.get(exp, exp)} ← 제목만 {STAGE_KO[got]}: {n}건")
        L.append("")
    return "\n".join(L)


def write_block(text: str) -> None:
    doc = VERIFICATION.read_text(encoding="utf-8")
    block = f"{START}\n{text}\n{END}"
    if START in doc and END in doc:
        doc = doc[: doc.index(START)] + block + doc[doc.index(END) + len(END):]
    else:
        doc = doc.rstrip() + "\n\n### 채점 결과 (규칙 엔진만)\n" + block + "\n"
    VERIFICATION.write_text(doc, encoding="utf-8")


def load_cases() -> list[dict]:
    cases = []
    for f in CASE_FILES:
        if f.exists():
            cases += json.loads(f.read_text(encoding="utf-8"))["cases"]
    return cases


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="docs/verification.md의 결과 칸을 바꾼다")
    args = ap.parse_args()
    text = render(evaluate(load_cases()))
    print(text)
    if args.write:
        write_block(text)
        print(f"\n저장: {VERIFICATION.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
