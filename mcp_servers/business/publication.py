"""Publish an immutable public assessment under the active worker's lease."""
from sqlalchemy import select
from shared.analysis import locked_job, current_manifest, utcnow
from shared.facts import facts_hash
from shared.models import PublicAssessment, Disclosure, RiskAssessment
from risk_engine.loader import load_rules
from risk_engine.states import update_state, score_fingerprint
from shared.schemas import ScoreResult

_FORBIDDEN = {'source_chunks','body_text','chunks','drafts','affected_customers','decisions','trace_headers'}


def clean_public(value):
    if isinstance(value, dict):
        return {k: clean_public(v) for k,v in value.items() if k not in _FORBIDDEN}
    if isinstance(value, list):return [clean_public(v) for v in value]
    return value


def complete_analysis(db, job_id, token, result, explanation, *, now=None):
    from harness.guardrails import check_explanation
    now=now or utcnow()
    job=locked_job(db,job_id,token,now=now)
    if not isinstance(explanation,str) or not explanation.strip() or check_explanation(explanation):
        raise ValueError('공개 설명 검증이 필요합니다.')
    parsed=ScoreResult.model_validate(result)
    if parsed.stage is None or parsed.manual_check:
        raise ValueError('완료 가능한 단계 판정이 필요합니다.')
    if result.get('as_of')!=job.as_of or result.get('rules_digest')!=job.rules_digest:
        raise ValueError('판정과 실행의 기준일/규칙 버전이 다릅니다.')
    import hashlib, json
    if hashlib.sha256(json.dumps(load_rules(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()!=job.rules_digest or facts_hash(current_manifest(db,job))!=job.input_hash:
        raise ValueError('분석 입력 또는 규칙이 변경됐습니다. 새 작업이 필요합니다.')
    if result.get('corp_code',job.corp_code)!=job.corp_code:
        raise ValueError('분석 회사가 다릅니다.')
    if result.get('expected_state_version') is None:
        raise ValueError('판정의 상태 버전 토큰이 필요합니다.')
    # Executors may orchestrate but cannot override deterministic R4 rule outputs.
    from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
    canonical=calculate_risk_score(job.corp_code,result.get('signals',[]),job.as_of)
    if not canonical['ok'] or score_fingerprint(canonical['data'])!=score_fingerprint(result):
        raise ValueError('계산 도구 결과와 공개 판정이 일치하지 않습니다.')
    if any(row.get('active') and not row.get('evidence') for row in result.get('breakdown',[])):
        raise ValueError('활성 위험의 검증 가능한 근거가 필요합니다.')
    allowed={'as_of','rules_digest','stage','active_stages','orange_score','yellow_score','composite_ratio',
             'sort_value','manual_check','manual_reasons','breakdown','signals','level','total_score',
             'combo_upgraded','expected_state_version','corp_code'}
    safe=clean_public({key:value for key,value in result.items() if key in allowed})
    # The active lease check, CAS, publication and completion are one transaction.
    disc=db.get(Disclosure,job.rcept_no)
    latest=max(d['filed_at'] for d in job.input_manifest['disclosures'])
    state=update_state(db,job.corp_code,safe,latest,now=now.isoformat())
    legacy=RiskAssessment(corp_code=job.corp_code,rcept_no=job.rcept_no,stage=parsed.stage,
        risk_state_version=state.state_version,score_fingerprint=None,total_score=parsed.total_score,
        level=parsed.level,combo_upgraded=parsed.combo_upgraded,reasons=safe.get('breakdown',[]),
        ai_explanation=explanation,status='DONE',created_at=now.isoformat())
    db.add(legacy);db.flush()
    assessment=PublicAssessment(assessment_id=job.job_key,job_id=job.job_id,corp_code=job.corp_code,
        rcept_no=job.rcept_no,as_of=job.as_of,input_hash=job.input_hash,rules_digest=job.rules_digest,
        legacy_assessment_id=legacy.assessment_id,result=safe,explanation=explanation,state_version=state.state_version,created_at=now.isoformat())
    db.add(assessment)
    state.public_assessment_id=assessment.assessment_id
    # A changed public result invalidates pending private drafts; existing tasks are untouched.
    from shared.models import OrganizationReview
    from sqlalchemy import update
    from risk_engine.stages import ALERT_STAGES
    previous=db.scalars(select(PublicAssessment.assessment_id).where(PublicAssessment.corp_code==job.corp_code)).all()
    if previous:
        db.execute(update(OrganizationReview).where(OrganizationReview.assessment_id.in_(previous),
            OrganizationReview.status.in_(['PENDING','RUNNING','WAITING_APPROVAL','MANUAL_CHECK'])).values(
                status='SUPERSEDED' if state.stage in ALERT_STAGES else 'RESOLVED',finished_at=now.isoformat(),
                lease_token=None,lease_expires_at=None))
    from shared.models import Signal
    from sqlalchemy import delete
    # Only current-disclosure normalized signals are replaced. Historical events remain intact.
    normalized=result.get('signals',[])
    if any(sig.get('rcept_no')!=job.rcept_no for sig in normalized):
        raise ValueError('현재 공시 이외의 신규 신호는 저장할 수 없습니다.')
    db.execute(delete(Signal).where(Signal.corp_code==job.corp_code,Signal.rcept_no==job.rcept_no))
    for sig in normalized:
        db.add(Signal(corp_code=job.corp_code,rcept_no=job.rcept_no,signal_type=sig['signal_type'],
            event_id=sig.get('event_id'),event_started_at=sig.get('event_started_at'),points=sig['points'],
            detail=clean_public(sig.get('detail',{})),filed_at=sig['filed_at']))
    disc.processed=True;disc.processed_at=now.isoformat()
    job.status='DONE';job.finished_at=now.isoformat();job.error_code=None
    job.lease_token=job.lease_expires_at=None
    db.flush()
    if state.stage in ALERT_STAGES:
        from shared.models import Customer, Holding
        from mcp_servers.business.reviews import create_review
        tenants=set(db.scalars(select(Customer.tenant_id).join(Holding,Holding.customer_id==Customer.customer_id).where(
            Holding.stock_code==state.stock_code,Holding.tenant_id==Customer.tenant_id)))
        for tenant in sorted(tenants):create_review(db,tenant,assessment.assessment_id)
    return assessment
