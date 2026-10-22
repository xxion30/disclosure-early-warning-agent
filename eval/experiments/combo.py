"""실험: 조합 규칙 켬/끔. 저장된 신호를 규칙에 다시 넣는다(AI 호출 없음). (담당: R4, 규칙 값은 R1 확정)"""
from experiments._common import NOT_READY_FIXTURE, load_fixture


OWNER = "R4"
TITLE = '조합 규칙 켬/끔 → 첫 경보가 사건보다 며칠 먼저'
NOTE = '입력은 eval/fixtures/signals.json (진짜 모델이 실제 DART 공시에서 찾은 신호). 켬 상태로 다시 계산한 등급이 원래 저장된 등급과 모두 같아(마지막 열) 재계산이 실제 실행을 그대로 재현한다.\n\n**결과: 켬/끔이 같다.** 실제 공시에서는 조합 조건(12개월 안에 서로 다른 신호 유형 3개 이상)을 채운 공시가 한 건도 없어 조합 규칙이 한 번도 발동하지 않았다. 금양·위니아의 조기 경보는 B그룹 점수 누적과 확정 악재 규칙(거래정지)이 만든 것이다. 조합 규칙의 동작 자체는 `tests/mcp_servers/test_rules.py`로 검증한다. 이 데이터로는 조합 규칙의 효과를 주장할 수 없다.'


def _replay_levels(comp: dict, rules: dict) -> list[dict]:
    """저장된 신호를 같은 순서로 규칙에 다시 넣어 공시마다의 등급을 구한다 (AI 호출 없음)."""
    from risk_engine import risk_rules

    by_rcept: dict[str, list[dict]] = {}
    for sg in comp["signals"]:
        by_rcept.setdefault(sg["rcept_no"], []).append(sg)
    out, history = [], []
    for a in comp["assessments"]:
        if not a["filed_at"]:
            continue
        mine = by_rcept.get(a["rcept_no"], [])
        new = [{"signal_type": g["signal_type"], "detail": g["detail"], "evidence": (g["detail"] or {}).get("evidence"),
                "filed_at": g["filed_at"], "rcept_no": g["rcept_no"]}
               for g in mine if not (g["detail"] or {}).get("synthetic")]  # 규칙이 만든 신호(정정 3회)는 다시 만들게 한다
        res = risk_rules.calculate(base_score=comp["base_score"] or 0, base_reasons=comp["base_reasons"] or [],
                                   history=history, new_signals=new, as_of=a["filed_at"], rules=rules)
        out.append({"rcept_no": a["rcept_no"], "filed_at": a["filed_at"], "level": res["level"],
                    "score": res["total_score"], "stored_level": a["level"], "combo_upgraded": res["combo_upgraded"]})
        history = history + [{**g, "points": g["points"]} for g in mine]
    return out


def run() -> list[dict]:
    import copy
    from datetime import date

    from risk_engine.loader import load_rules

    data = load_fixture()
    if not data:
        return NOT_READY_FIXTURE
    on = load_rules()
    off = copy.deepcopy(on)
    off["combo"]["enabled"] = False
    order = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
    rows = []
    for stock, comp in data.items():
        event = comp.get("event") or {}
        ev_date = (event.get("date") or "")[:10]
        for label, rules in (("조합 규칙 켬", on), ("조합 규칙 끔", off)):
            seq = _replay_levels(comp, rules)
            before = [x for x in seq if not ev_date or x["filed_at"] < ev_date]
            first = next((x for x in before if order[x["level"]] >= order["High"]), None)
            ev = next((x for x in seq if x["rcept_no"] == event.get("rcept_no")), None)
            days = (date.fromisoformat(ev_date) - date.fromisoformat(first["filed_at"])).days if first and ev_date else None
            rows.append({
                "회사": comp["name"], "설정": label,
                "사건 전 첫 High 이상": f"{first['filed_at']} {first['level']}" if first else "없음",
                "사건보다 먼저(일)": days if days is not None else "-",
                "사건 전 High 이상 공시 수": sum(1 for x in before if order[x["level"]] >= order["High"]),
                "조합으로 올린 공시 수": sum(1 for x in seq if x["combo_upgraded"]),
                "사건 공시 등급": ev["level"] if ev else "-",
                "저장된 등급과 일치": f"{sum(1 for x in seq if x['level'] == x['stored_level'])}/{len(seq)}" if rules is on else "-",
            })
    return rows
