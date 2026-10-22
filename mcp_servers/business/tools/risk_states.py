from sqlalchemy import select
from shared.db import get_session
from shared.models import RiskState
from shared.schemas import ok
from mcp_servers.business.tools._common import guarded, require_company
from risk_engine.states import update_state


@guarded
def get_risk_state(corp_code: str) -> dict:
    with get_session() as s:
        require_company(s, corp_code)
        row = s.get(RiskState, corp_code)
        return ok(None if row is None else {c.name: getattr(row, c.name) for c in row.__table__.columns})


@guarded
def save_risk_state(corp_code: str, score_result: dict, filed_at: str, resolution_evidence: list | None = None) -> dict:
    from risk_engine.risk_rules import parse_date
    with get_session() as s:
        require_company(s, corp_code)
        row = update_state(s, corp_code, score_result, parse_date(filed_at).isoformat(), resolution_evidence=resolution_evidence)
        return ok({'corp_code': corp_code, 'stage': row.stage, 'alert_version': row.alert_version})
