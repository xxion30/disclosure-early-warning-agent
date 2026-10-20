from mcp_servers.business.tools._common import guarded, require_company
from shared.db import get_session
from shared.schemas import ok


@guarded
def get_company_profile(corp_code: str) -> dict:
    """회사 정보와 사업보고서 기본 점수(base_score, base_reasons)를 돌려준다.

    Args:
        corp_code: DART 고유번호
    """
    with get_session() as s:
        c = require_company(s, corp_code)
        return ok(
            {
                "corp_code": c.corp_code,
                "stock_code": c.stock_code,
                "name": c.name,
                "market": c.market,
                "base_score": c.base_score,
                "base_reasons": c.base_reasons or [],
            }
        )
