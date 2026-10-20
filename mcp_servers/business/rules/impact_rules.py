"""compute_customer_impact 계산 로직 (CLAUDE.md 5-5장). 숫자는 rules.yaml에서 읽는다."""
from __future__ import annotations

from mcp_servers.business.rules.loader import load_rules


class ImpactInputError(ValueError):
    pass


def _step(table: list[dict], value: float) -> int:
    for row in table:
        if value >= row["min"]:
            return row["score"]
    return 0


def customer_impact(
    *,
    eval_amount: int,
    total_assets: int,
    risk_profile: str,
    on_credit: bool,
    company_level: str,
    rules: dict | None = None,
) -> dict:
    rules = rules or load_rules()
    cfg = rules["impact"]
    if company_level not in cfg["priority"]:
        raise ImpactInputError(
            f"level은 {', '.join(cfg['priority'])} 중 하나여야 합니다 (고객 영향도는 High 이상에서만 계산): {company_level!r}"
        )
    if risk_profile not in cfg["risk_profile"]:
        raise ImpactInputError(f"알 수 없는 투자성향: {risk_profile!r}")

    ratio = eval_amount / total_assets if total_assets else 0.0
    parts = {
        "holding_ratio": _step(cfg["holding_ratio"], ratio),
        "eval_amount": _step(cfg["eval_amount"], eval_amount),
        "risk_profile": cfg["risk_profile"][risk_profile],
        "on_credit": cfg["on_credit_score"] if on_credit else 0,
    }
    score = sum(parts.values())
    impact_level = next(r["level"] for r in cfg["levels"] if score >= r["min"])
    if on_credit:
        impact_level = cfg["credit_forces_level"]
    priority = cfg["priority"][company_level][impact_level]
    return {
        "impact_score": score,
        "impact_level": impact_level,
        "priority": priority,
        "reasons": {**parts, "holding_ratio_pct": round(ratio * 100, 1)},
    }
