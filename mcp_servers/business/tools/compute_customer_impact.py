from risk_engine import impact_rules
from risk_engine.loader import load_rules
from mcp_servers.business.tools._common import guarded, invalid
from mcp_servers.business.tools.get_holders import holders_of
from shared.db import get_session
from shared.schemas import ok


@guarded
def compute_customer_impact(stock_code: str, level: str) -> dict:
    """종목 보유 고객별 영향도 점수·등급·연락 우선순위(P1/P2/P3)를 규칙표로 계산한다.

    Args:
        stock_code: 종목코드 6자리
        level: 회사 위험 등급 (High 또는 Critical)

    Returns:
        customers(우선순위·점수 순), counts, contact_targets(P1·P2 고객 id)
    """
    if not isinstance(stock_code, str) or len(stock_code) != 6 or not stock_code.isalnum():
        raise invalid("stock_code는 6자리 종목코드여야 합니다. 예: 001570")
    rules = load_rules()
    contact = rules["impact"]["contact_priorities"]
    with get_session() as s:
        rows = []
        for h, c in holders_of(s, stock_code):
            r = impact_rules.customer_impact(
                eval_amount=h.eval_amount,
                total_assets=c.total_assets,
                risk_profile=c.risk_profile,
                on_credit=h.on_credit,
                company_level=level,
                rules=rules,
            )
            rows.append({"customer_id": c.customer_id, **r})
    order = {p: i for i, p in enumerate(["P1", "P2", "P3"])}
    rows.sort(key=lambda r: (order[r["priority"]], -r["impact_score"], r["customer_id"]))
    counts = {p: sum(1 for r in rows if r["priority"] == p) for p in ("P1", "P2", "P3")}
    return ok(
        {
            "stock_code": stock_code,
            "level": level,
            "customers": rows,
            "counts": counts,
            "contact_targets": [r["customer_id"] for r in rows if r["priority"] in contact],
        }
    )
