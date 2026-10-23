"""Validate the public risk version independently of private Graph checkpoints."""
from fastapi import HTTPException
from sqlalchemy import select
from shared.models import Company, RiskState
from risk_engine.stages import ALERT_STAGES


def require_current_review(db, assessment, view=None):
    # Same lock order as the risk-state writer. Caller must keep this transaction until decision acceptance.
    db.scalar(select(Company).where(Company.corp_code == assessment.corp_code).with_for_update())
    state = db.scalar(select(RiskState).where(RiskState.corp_code == assessment.corp_code)
                      .with_for_update().execution_options(populate_existing=True))
    if state is None:
        return  # legacy pre-stage assessments only; new assessments always create a RiskState
    if state.resolved or state.stage not in ALERT_STAGES:
        raise HTTPException(409, '위험이 해소되거나 경보 대상에서 제외되었습니다. 기존 초안을 승인할 수 없습니다.')
    if assessment.risk_state_version is None or assessment.risk_state_version != state.state_version:
        raise HTTPException(409, '초안 이후 위험 상태가 변경되었습니다. 최신 판정으로 초안을 다시 검토해야 합니다.')
    if assessment.score_fingerprint is not None and (view or {}).get('review_fingerprint') != assessment.score_fingerprint:
        raise HTTPException(409, '실행 중인 초안과 저장된 판정이 일치하지 않습니다. 초안을 다시 생성해야 합니다.')
