from sqlalchemy import select

from mcp_servers.business.tools._common import guarded, invalid
from shared.db import get_session
from shared.models import Customer, Holding
from shared.schemas import ok


def masked_name(customer_id: str) -> str:
    """LLM은 실제 고객 이름을 보지 않는다 (CLAUDE.md 6장 Guardrails)."""
    return f"고객 {customer_id}"


def holders_of(s, stock_code: str, tenant_id: str = "default") -> list[tuple[Holding, Customer]]:
    return s.execute(
        select(Holding, Customer)
        .join(Customer, Customer.customer_id == Holding.customer_id)
        .where(Holding.stock_code == stock_code, Holding.tenant_id == tenant_id, Customer.tenant_id == tenant_id)
        .order_by(Holding.eval_amount.desc(), Holding.customer_id)
    ).all()


@guarded
def get_holders(stock_code: str, tenant_id: str = "default") -> dict:
    """해당 종목을 보유한 고객과 보유 내역. 이름은 '고객 C001' 형태로 가려서 돌려준다.

    Args:
        stock_code: 종목코드 6자리 (예: 001570)
    """
    if not isinstance(stock_code, str) or len(stock_code) != 6 or not stock_code.isalnum():
        raise invalid("stock_code는 6자리 종목코드여야 합니다. 예: 001570")
    with get_session() as s:
        holders = [
            {
                "customer_id": c.customer_id,
                "name": masked_name(c.customer_id),
                "risk_profile": c.risk_profile,
                "total_assets": c.total_assets,
                "eval_amount": h.eval_amount,
                "holding_ratio_pct": round(h.eval_amount / c.total_assets * 100, 1) if c.total_assets else None,
                "on_credit": h.on_credit,
            }
            for h, c in holders_of(s, stock_code, tenant_id)
        ]
        return ok({"stock_code": stock_code, "count": len(holders), "holders": holders})
