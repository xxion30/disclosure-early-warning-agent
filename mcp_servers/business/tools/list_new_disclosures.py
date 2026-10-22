from sqlalchemy import select

from risk_engine.risk_rules import parse_date
from mcp_servers.business.tools._common import guarded
from shared.db import get_session
from shared.models import Company, Disclosure
from shared.schemas import ok

MAX_ROWS = 200


@guarded
def list_new_disclosures(since: str) -> dict:
    """아직 처리하지 않은 공시 목록을 접수일 순서로 돌려준다 (원문 제외).

    Args:
        since: 이 날짜(YYYY-MM-DD) 이후에 접수된 공시만. 예: 2025-04-01
    """
    since_d = parse_date(since).isoformat()
    with get_session() as s:
        rows = s.execute(
            select(Disclosure, Company)
            .join(Company, Company.corp_code == Disclosure.corp_code)
            .where(Disclosure.processed.is_(False), Disclosure.filed_at >= since_d)
            .order_by(Disclosure.filed_at, Disclosure.rcept_no)
            .limit(MAX_ROWS + 1)
        ).all()
        items = [
            {
                "rcept_no": d.rcept_no,
                "corp_code": d.corp_code,
                "stock_code": c.stock_code,
                "corp_name": c.name,
                "title": d.title,
                "disclosure_type": d.disclosure_type,
                "filed_at": d.filed_at,
            }
            for d, c in rows[:MAX_ROWS]
        ]
        return ok({"count": len(items), "truncated": len(rows) > MAX_ROWS, "disclosures": items})
