"""Validate source references, not model-generated financial values."""
from datetime import date
from sqlalchemy import select
from shared.db import get_session
from shared.models import Disclosure, FinancialSnapshot
from shared.financial_evidence import FinancialEvidence, snapshot_reference
from shared.schemas import ok
from mcp_servers.business.tools._common import guarded, invalid


@guarded
def verify_financial_evidence(corp_code: str, as_of: str, evidence: list[dict]) -> dict:
    day = date.fromisoformat(as_of).isoformat()
    verified = []
    with get_session() as db:
        for raw in evidence:
            ref = FinancialEvidence.model_validate(raw)
            disclosure = db.get(Disclosure, ref.rcept_no)
            if (ref.corp_code != corp_code or disclosure is None or disclosure.corp_code != corp_code
                    or disclosure.filed_at[:10] > day):
                raise invalid('재무 근거의 회사·공시일 불일치')
            rows = db.scalars(select(FinancialSnapshot).where(
                FinancialSnapshot.corp_code == corp_code, FinancialSnapshot.rcept_no == ref.rcept_no,
                FinancialSnapshot.period == ref.period, FinancialSnapshot.basis == ref.basis,
                FinancialSnapshot.revision == ref.revision)).all()
            if len(rows) != 1 or ref.indicator not in rows[0].indicators:
                raise invalid('재무 근거 스냅샷을 식별할 수 없습니다')
            expected = snapshot_reference(rows[0], ref.indicator)
            if expected != ref.model_dump():
                raise invalid('재무 근거의 값 또는 원천 버전 불일치')
            verified.append(expected)
    return ok({'evidence': verified})
