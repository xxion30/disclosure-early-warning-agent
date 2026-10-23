"""Recalculate time weights without inventing a new disclosure or marking risk resolved."""
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select
from shared.db import get_session
from shared.models import Company, RiskState
from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
from risk_engine.states import update_state


def refresh(as_of=None):
    as_of = as_of or datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    with get_session() as s:
        companies = [(c.corp_code, s.get(RiskState,c.corp_code).latest_filed_at) for c in s.scalars(select(Company)) if s.get(RiskState,c.corp_code)]
    for corp, latest in companies:
        result = calculate_risk_score(corp, [], as_of)
        if not result['ok']:
            raise RuntimeError(f'{corp}: {result["error"]}')
        with get_session() as s:
            update_state(s,corp,result['data'],latest)
    return len(companies)


if __name__ == '__main__':
    print(f'refreshed={refresh()}')
