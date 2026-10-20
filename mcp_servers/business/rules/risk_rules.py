"""calculate_risk_score 계산 로직. DB를 건드리지 않는 순수 함수라 단위 테스트가 쉽다.

규칙표 숫자는 전부 rules.yaml에서 읽는다. AI가 넘기는 것은 신호 종류와 원문에서 찾은 값(detail)뿐이고,
점수는 여기서만 정해진다.
"""
from __future__ import annotations

import calendar
from datetime import date

from mcp_servers.business.rules.loader import load_rules


class RuleInputError(ValueError):
    """도구 입력이 규칙표와 맞지 않을 때 (INVALID_INPUT으로 바뀐다)."""


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as e:
        raise RuleInputError(f"날짜 형식이 올바르지 않습니다: {value!r} (예: 2025-04-01)") from e


def minus_months(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + (d.month - 1) - months, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def level_of_score(score: float, rules: dict) -> str:
    for row in rules["alert_levels"]:
        if score >= row["min_score"]:
            return row["level"]
    return rules["level_order"][0]


def shift_level(level: str, steps: int, rules: dict) -> str:
    order = rules["level_order"]
    return order[min(max(order.index(level) + steps, 0), len(order) - 1)]


def max_level(a: str | None, b: str | None, rules: dict) -> str | None:
    if a is None or b is None:
        return a or b
    order = rules["level_order"]
    return a if order.index(a) >= order.index(b) else b


def time_weight(filed: date, as_of: date, rules: dict) -> float:
    """as_of 기준 공시일의 반영 비율. 구간 밖(12개월 초과)이거나 as_of보다 미래이면 0."""
    if filed > as_of:
        return 0.0
    for row in rules["time_weights"]:
        if filed >= minus_months(as_of, row["within_months"]):
            return float(row["weight"])
    return 0.0


def _truthy(detail: dict, key: str) -> bool:
    v = detail.get(key)
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "yes", "y", "예", "해당"))


def _num(detail: dict, key: str) -> float | None:
    v = detail.get(key)
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ A그룹 (확정 악재)
def evaluate_group_a(signal_type: str, detail: dict, rules: dict) -> dict:
    """반환: {fixed_level: str|None, note: str|None}"""
    spec = rules["group_a"][signal_type]
    flag = spec.get("ignore_if_flag")
    if flag and _truthy(detail, flag):
        return {"fixed_level": None, "note": f"{spec['label']} 해제·취소 공시라 등급에 반영하지 않음"}
    kind = spec["kind"]
    if kind == "fixed":
        return {"fixed_level": spec["level"], "note": None}
    if kind == "map":
        value = str(detail.get(spec["key"]) or "").strip()
        level = next((lv for word, lv in spec["levels"].items() if word in value), None)
        if level is None and not value:
            return {"fixed_level": None, "note": f"{spec['label']} 값을 찾지 못함 — 수동 확인 필요"}
        return {"fixed_level": level, "note": None}
    if kind == "ratio_threshold":
        ratio = _num(detail, spec["key"])
        if ratio is None:
            return {
                "fixed_level": spec["unknown"],
                "note": f"{spec['label']} 비율을 찾지 못함 — 수동 확인 필요",
            }
        market = str(detail.get(spec["market_key"]) or "")
        limits = spec["threshold_pct"]
        limit = limits.get(market, limits["default"])
        return {
            "fixed_level": spec["at_or_above"] if ratio >= limit else spec["below"],
            "note": f"자기자본 대비 {ratio:g}% (기준 {limit:g}%)",
        }
    if kind == "min_levels":
        value = _num(detail, spec["key"])
        if value is None:
            return {"fixed_level": None, "note": f"{spec['label']} 비율을 찾지 못함 — 수동 확인 필요"}
        for row in spec["levels"]:
            if value >= row["min"]:
                return {"fixed_level": row["level"], "note": f"잠식률 {value:g}%"}
        return {"fixed_level": None, "note": None}
    raise RuleInputError(f"알 수 없는 A그룹 규칙 종류: {kind}")


# ------------------------------------------------------------------ B그룹 (숨은 신호)
def _cond_met(cond: dict, detail: dict, repeat_count: int) -> bool:
    if "flag" in cond:
        return _truthy(detail, cond["flag"])
    if "gte" in cond:
        v = _num(detail, cond["gte"]["key"])
        return v is not None and v >= cond["gte"]["value"]
    if "repeat_min" in cond:
        return repeat_count >= cond["repeat_min"]
    return False


def evaluate_group_b(signal_type: str, detail: dict, repeat_count: int, rules: dict) -> dict:
    """반환: {base, extra, extras: [이름], note}. 가중치 적용 전 원점수."""
    spec = rules["group_b"][signal_type]
    cond = spec.get("apply_if")
    if cond:
        v = _num(detail, cond["key"])
        if v is None:
            return {"base": 0, "extra": 0, "extras": [], "note": f"{spec['label']} {cond['key']} 값을 찾지 못함 — 수동 확인 필요"}
        if v < cond["gte"]:
            return {"base": 0, "extra": 0, "extras": [], "note": f"{cond['key']} {v:g} < {cond['gte']} 기준 미만이라 0점"}
    flag = spec.get("zero_if_flag")
    if flag and _truthy(detail, flag):
        return {"base": 0, "extra": 0, "extras": [], "note": f"{spec['label']}: 예외 사유({flag})라 0점"}
    extras, extra = [], 0.0
    for e in spec.get("extras", []):
        if any(_cond_met(c, detail, repeat_count) for c in e["any"]):
            extras.append(e["name"])
            extra += e["add"]
    return {"base": spec["base"], "extra": extra, "extras": extras, "note": None}


# ------------------------------------------------------------------ 전체 계산
def calculate(
    *,
    base_score: float,
    base_reasons: list,
    history: list[dict],
    new_signals: list[dict],
    as_of: str,
    rules: dict | None = None,
) -> dict:
    """history: signals 테이블 행(dict). new_signals: {signal_type, detail, filed_at?, rcept_no?}."""
    rules = rules or load_rules()
    as_of_d = parse_date(as_of)
    window_start = minus_months(as_of_d, rules["history_months"])
    group_a, group_b = rules["group_a"], rules["group_b"]

    # 같은 rcept_no를 다시 돌릴 때 이전 기록과 겹쳐 세지 않도록 history에서 뺀다.
    new_rcepts = {s.get("rcept_no") for s in new_signals if s.get("rcept_no")}
    past = [h for h in history if h.get("rcept_no") not in new_rcepts]

    normalized: list[dict] = []
    for s in new_signals:
        st = s.get("signal_type")
        if st not in group_a and st not in group_b:
            allowed = ", ".join(sorted([*group_a, *group_b]))
            raise RuleInputError(f"알 수 없는 signal_type: {st!r}. 허용: {allowed}")
        filed = str(s.get("filed_at") or as_of)[:10]
        parse_date(filed)
        normalized.append(
            {
                "signal_type": st,
                "detail": dict(s.get("detail") or {}),
                "filed_at": filed,
                "rcept_no": s.get("rcept_no"),
            }
        )

    # 12개월 안의 같은 유형 개수(이번 신호 포함) — CB·BW "12개월 내 2회 이상"에 쓴다.
    def count_in_window(signal_type: str, upto: date) -> int:
        n = 0
        for h in past:
            fd = parse_date(h["filed_at"])
            if h["signal_type"] == signal_type and h.get("points", 0) > 0 and window_start <= fd <= upto:
                n += 1
        return n

    breakdown: list[dict] = []
    floors: list[str] = []  # A그룹 등급 하한
    seen_new_b: dict[str, int] = {}

    # 1) 기본 점수 (사업보고서)
    if base_score:
        breakdown.append(
            {
                "group": "BASE",
                "item": "사업보고서 기본 점수",
                "signal_type": "BASE_SCORE",
                "raw_points": base_score,
                "weight": 1.0,
                "points": float(base_score),
                "reasons": base_reasons,
                "quote": None,
                "note": None,
            }
        )

    # 2) 새 신호
    for sig in normalized:
        st, detail = sig["signal_type"], sig["detail"]
        filed_d = parse_date(sig["filed_at"])
        quote = detail.get("quote")
        if st in group_a:
            res = evaluate_group_a(st, detail, rules)
            sig["points"] = 0.0
            sig["detail"] = {**detail, "fixed_level": res["fixed_level"]}
            if res["fixed_level"]:
                floors.append(res["fixed_level"])
            breakdown.append(
                {
                    "group": "A",
                    "item": group_a[st]["label"],
                    "signal_type": st,
                    "filed_at": sig["filed_at"],
                    "rcept_no": sig["rcept_no"],
                    "raw_points": 0,
                    "weight": 1.0,
                    "points": 0.0,
                    "fixed_level": res["fixed_level"],
                    "quote": quote,
                    "note": res["note"],
                }
            )
            continue
        repeat = count_in_window(st, filed_d) + seen_new_b.get(st, 0) + 1
        res = evaluate_group_b(st, detail, repeat, rules)
        raw = float(res["base"] + res["extra"])
        weight = time_weight(filed_d, as_of_d, rules)
        if raw > 0:
            seen_new_b[st] = seen_new_b.get(st, 0) + 1
        sig["points"] = raw
        sig["detail"] = {**detail, "extras": res["extras"]}
        breakdown.append(
            {
                "group": "B",
                "item": group_b[st]["label"],
                "signal_type": st,
                "filed_at": sig["filed_at"],
                "rcept_no": sig["rcept_no"],
                "base_points": res["base"],
                "extra_points": res["extra"],
                "extras": res["extras"],
                "raw_points": raw,
                "weight": weight,
                "points": raw * weight,
                "quote": quote,
                "note": res["note"],
            }
        )

    # 3) 과거 신호 (시간 반영)
    for h in past:
        st = h["signal_type"]
        filed_d = parse_date(h["filed_at"])
        weight = time_weight(filed_d, as_of_d, rules)
        if weight == 0.0:
            continue
        detail = h.get("detail") or {}
        if st in group_a:
            fixed = detail.get("fixed_level")
            if fixed:
                floors.append(fixed)
            breakdown.append(
                {
                    "group": "A",
                    "item": group_a[st]["label"] if st in group_a else st,
                    "signal_type": st,
                    "filed_at": h["filed_at"],
                    "rcept_no": h.get("rcept_no"),
                    "raw_points": 0,
                    "weight": 1.0,
                    "points": 0.0,
                    "fixed_level": fixed,
                    "quote": detail.get("quote"),
                    "note": "과거 확정 악재",
                    "from_history": True,
                }
            )
            continue
        raw = float(h.get("points") or 0)
        breakdown.append(
            {
                "group": "B",
                "item": group_b.get(st, {}).get("label", st),
                "signal_type": st,
                "filed_at": h["filed_at"],
                "rcept_no": h.get("rcept_no"),
                "extras": detail.get("extras", []),
                "raw_points": raw,
                "weight": weight,
                "points": raw * weight,
                "quote": detail.get("quote"),
                "note": "과거 신호",
                "from_history": True,
            }
        )

    total = round(sum(b["points"] for b in breakdown), 2)
    level = level_of_score(total, rules)
    floor = None
    for f in floors:
        floor = max_level(floor, f, rules)
    if floor:
        level = max_level(level, floor, rules)

    # 4) 조합 규칙: 12개월 안에 서로 다른 B그룹 signal_type이 N개 이상이면 한 단계 상향
    combo_cfg = rules["combo"]
    combo_upgraded = False
    distinct = sorted(
        {b["signal_type"] for b in breakdown if b["group"] == "B" and b["raw_points"] > 0}
    )
    if combo_cfg["enabled"] and len(distinct) >= combo_cfg["min_distinct_types"]:
        upgraded = shift_level(level, combo_cfg["upgrade_steps"], rules)
        combo_upgraded = upgraded != level
        level = upgraded

    return {
        "total_score": total,
        "level": level,
        "combo_upgraded": combo_upgraded,
        "base_score": base_score,
        "fixed_level": floor,
        "distinct_signal_types": distinct,
        "breakdown": breakdown,
        "signals": [
            {
                "signal_type": s["signal_type"],
                "points": s["points"],
                "detail": s["detail"],
                "filed_at": s["filed_at"],
                "rcept_no": s["rcept_no"],
            }
            for s in normalized
        ],
    }
