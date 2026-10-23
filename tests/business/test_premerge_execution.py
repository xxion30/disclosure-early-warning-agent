"""Execution protocol fixtures; no new real-company DART facts are asserted."""
import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select
import shared.db as db_module
from shared.analysis import (mark_ready,enqueue,claim,heartbeat,recover_expired,fail_job,LostLease)
from shared.models import (Disclosure,AnalysisJob,PublicAssessment,OrganizationReview,RiskState,
                           Tenant,PB,Customer,Holding,ContactTask)
from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
from mcp_servers.business.publication import complete_analysis
from mcp_servers.business.reviews import create_review,claim_review,save_drafts,decide,scoped_review

RCEPT='20250321802001'
DAY='2025-03-21'


def score():
    with db_module.get_session() as db:corp=db.get(Disclosure,RCEPT).corp_code
    response=calculate_risk_score(corp,[{'signal_type':'AUDIT_OPINION','rcept_no':RCEPT,
        'detail':{'opinion':'거절','going_concern':False},'evidence':[{'source':'contract-fixture','quote':'감사의견 의견거절',
        'rcept_no':RCEPT,'location':'contract-fixture'}]}],DAY)
    assert response['ok'],response
    return response['data']


def prepare(result):
    with db_module.get_session() as db:
        mark_ready(db,RCEPT)
        job=enqueue(db,RCEPT,DAY,result['rules_digest'])
        job_id=job.job_id
    return job_id


def publish():
    result=score();job_id=prepare(result)
    with db_module.get_session() as db:
        job=claim(db);assert job.job_id==job_id
        public=complete_analysis(db,job_id,job.lease_token,result,'감사 공시 근거를 확인해 주세요.')
        return public.assessment_id


def test_handoff_is_required_and_enqueue_is_idempotent(seeded_db):
    result=score()
    with db_module.get_session() as db:
        with pytest.raises(ValueError):enqueue(db,RCEPT,DAY,result['rules_digest'])
        mark_ready(db,RCEPT)
        a=enqueue(db,RCEPT,DAY,result['rules_digest']);b=enqueue(db,RCEPT,DAY,result['rules_digest'])
        assert a.job_id==b.job_id
        first=claim(db);assert first.attempts==1
        assert claim(db) is None


def test_lease_expiry_and_three_attempt_limit(seeded_db,monkeypatch):
    from shared.config import get_settings
    monkeypatch.setenv('ANALYSIS_RETRY_DELAY_SECONDS','0');get_settings.cache_clear()
    prepare(score())
    clock=datetime.now(timezone.utc)
    with db_module.get_session() as db:
        first=claim(db,now=clock);job_id=first.job_id;old=first.lease_token
        later=clock+timedelta(seconds=121)
        assert not heartbeat(db,job_id,old,now=later)
        assert recover_expired(db,now=later)==1
        second=claim(db,now=later);assert second.attempts==2
        with pytest.raises(LostLease):fail_job(db,job_id,old,'old',now=later)
        assert fail_job(db,job_id,second.lease_token,'TIMEOUT',retryable=True,now=later)=='RETRY_WAIT'
        third=claim(db,now=later);assert third.attempts==3
        assert fail_job(db,job_id,third.lease_token,'TIMEOUT',retryable=True,now=later)=='FAILED'
        assert claim(db,now=later) is None
    get_settings.cache_clear()


def test_public_completion_is_separate_from_reviews_and_has_no_raw_chunks(seeded_db):
    result=score();result['breakdown'][0]['source_chunks']=[{'text':'unwanted-full-source'}]
    job_id=prepare(result)
    with db_module.get_session() as db:
        job=claim(db)
        public=complete_analysis(db,job_id,job.lease_token,result,'감사 공시 근거를 확인해 주세요.')
        assert 'unwanted-full-source' not in str(public.result)
        reviews=db.scalars(select(OrganizationReview).where(OrganizationReview.assessment_id==public.assessment_id)).all()
        assert reviews and all(r.status=='PENDING' for r in reviews)
        assert db.get(AnalysisJob,job_id).status=='DONE'
        assert db.get(Disclosure,RCEPT).processed_at
        assert db.scalar(select(ContactTask)) is None


def test_changed_input_cannot_publish(seeded_db):
    result=score();job_id=prepare(result)
    with db_module.get_session() as db:
        job=claim(db);token=job.lease_token
        db.get(Disclosure,RCEPT).title+=' changed'
    with db_module.get_session() as db:
        with pytest.raises(ValueError,match='입력'):complete_analysis(db,job_id,token,result,'공시를 확인해 주세요.')
        assert db.scalar(select(PublicAssessment)) is None


def test_forged_financial_missing_reason_cannot_publish_an_unlinked_correction(seeded_db):
    correction = '20241018000341'  # Existing actual DART correction metadata.
    with db_module.get_session() as db:
        disc = db.get(Disclosure, correction)
        corp, day = disc.corp_code, disc.filed_at[:10]
    result = calculate_risk_score(corp,[{'signal_type':'RIGHTS_OFFERING','rcept_no':correction,'detail':{}}],day)['data']
    assert result['manual_check']
    result['manual_reasons'] = [{'signal_type':'INTEREST_BURDEN','missing':['interest_coverage']}]
    with db_module.get_session() as db:
        mark_ready(db,correction)
        job=enqueue(db,correction,day,result['rules_digest'])
        claimed=claim(db)
        with pytest.raises(ValueError):
            complete_analysis(db,job.job_id,claimed.lease_token,result,'공시 확인이 필요합니다.')
        assert db.scalar(select(PublicAssessment)) is None


def test_daily_recalculation_keeps_equivalent_review_and_does_not_requeue_forever(seeded_db):
    from shared.analysis import enqueue_recalculations
    from mcp_servers.business.reviews import matching_review
    aid = publish()
    with db_module.get_session() as db:
        public = db.get(PublicAssessment, aid)
        review = db.scalar(select(OrganizationReview).where(OrganizationReview.assessment_id == aid))
        rid, original_version = review.review_id, public.state_version
        claimed = claim_review(db, rid, review.tenant_id)
        customers = db.scalars(select(Customer.customer_id).join(Holding,
            Holding.customer_id == Customer.customer_id).where(Holding.stock_code == '001570')).all()
        save_drafts(db, rid, review.tenant_id, claimed.lease_token,
            {cid:'{고객명}님, 공시 내용을 확인하고 PB와 상담해 주세요.' for cid in customers})
        jobs = enqueue_recalculations(db, '2025-03-22', public.rules_digest)
        assert len(jobs) == 1 and jobs[0].as_of == '2025-03-22'
        assert enqueue_recalculations(db, '2025-03-22', public.rules_digest) == []
        job_id = jobs[0].job_id
    result = calculate_risk_score(public.corp_code, public.result['signals'], '2025-03-22')['data']
    with db_module.get_session() as db:
        job = claim(db)
        assert job.job_id == job_id
        fresh = complete_analysis(db, job.job_id, job.lease_token, result, '공시 근거를 확인해 주세요.')
        assert fresh.state_version == original_version
        assert matching_review(db, review.tenant_id, fresh).review_id == rid
        assert db.get(OrganizationReview, rid).status == 'WAITING_APPROVAL'
        assert enqueue_recalculations(db, '2025-03-22', fresh.rules_digest) == []


def test_backfill_publishes_history_without_rolling_back_current_state(seeded_db):
    from tests.wiring.test_unified_facts_mapping import inputs
    from agent.adapters.unified import prepare_signals
    from shared.facts import store_facts
    from shared.analysis import enqueue_recalculations
    from shared.models import Signal
    payload = inputs()
    receipt = payload['disclosure']['rcept_no']
    with db_module.get_session() as db:
        store_facts(db, receipt, payload['structured_facts'])
    aid = publish()
    with db_module.get_session() as db:
        public = db.get(PublicAssessment, aid)
        previous_version = public.state_version
        review = db.scalar(select(OrganizationReview).where(OrganizationReview.assessment_id == aid))
        rid = review.review_id
        mark_ready(db, receipt)
    historical = calculate_risk_score(public.corp_code, prepare_signals(payload), payload['as_of'])['data']
    with db_module.get_session() as db:
        job = enqueue(db, receipt, payload['as_of'], historical['rules_digest'])
        claimed = claim(db)
        assert claimed.job_id == job.job_id
        old = complete_analysis(db, job.job_id, claimed.lease_token, historical, '과거 공시 근거를 확인해 주세요.')
        assert old.state_version == 0
        state = db.get(RiskState, public.corp_code)
        assert state.public_assessment_id == aid and state.state_version == previous_version
        assert db.get(OrganizationReview, rid).status == 'PENDING'
        assert db.scalar(select(Signal).where(Signal.rcept_no == receipt)) is not None
        with pytest.raises(ValueError, match='최신'):
            create_review(db, review.tenant_id, old.assessment_id)
        # Same-day history changes must still trigger a current-day reassessment.
        pending = enqueue_recalculations(db, DAY, public.rules_digest)
        assert len(pending) == 1 and pending[0].rcept_no == RCEPT


def add_second_org(db,stock):
    db.add(Tenant(tenant_id='org-two',name='org-two'))
    db.add(PB(pb_id='pb-two',tenant_id='org-two',name='fixture',email='pb-two@example.invalid',password_hash='unused'));db.flush()
    db.add(Customer(customer_id='customer-two',tenant_id='org-two',pb_id='pb-two',name='fixture',risk_profile='fixture',total_assets=1));db.flush()
    db.add(Holding(customer_id='customer-two',tenant_id='org-two',stock_code=stock,eval_amount=1));db.flush()


def test_org_isolation_and_atomic_idempotent_approval(seeded_db):
    aid=publish()
    with db_module.get_session() as db:
        public=db.get(PublicAssessment,aid);stock=db.get(RiskState,public.corp_code).stock_code
        add_second_org(db,stock)
        row=create_review(db,'org-two',aid);review_id=row.review_id
        assert create_review(db,'org-two',aid).review_id==review_id
        claimed=claim_review(db,review_id,'org-two');token=claimed.lease_token
        save_drafts(db,review_id,'org-two',token,{'customer-two':'{고객명}님, 공시 내용을 확인하시고 PB와 상담해 주세요.'})
        version=row.version
        with pytest.raises(PermissionError):scoped_review(db,review_id,'default')
        with pytest.raises(PermissionError):decide(db,review_id,'org-two','P01',[{'customer_id':'customer-two','action':'approve'}],version)
        decisions=[{'customer_id':'customer-two','action':'approve'}]
        decide(db,review_id,'org-two','pb-two',decisions,version)
        decide(db,review_id,'org-two','pb-two',decisions,version)
        assert len(db.scalars(select(ContactTask).where(ContactTask.review_id==review_id)).all())==1
        assert db.get(AnalysisJob,public.job_id).status=='DONE'


def test_old_draft_is_rejected_after_public_state_changes(seeded_db):
    aid=publish()
    with db_module.get_session() as db:
        public=db.get(PublicAssessment,aid);stock=db.get(RiskState,public.corp_code).stock_code
        add_second_org(db,stock)
        row=create_review(db,'org-two',aid);rid=row.review_id
        row=claim_review(db,rid,'org-two')
        save_drafts(db,rid,'org-two',row.lease_token,{'customer-two':'{고객명}님, 공시 내용을 확인해 주세요.'})
        db.get(RiskState,public.corp_code).resolved=True
        with pytest.raises(ValueError,match='최신'):
            decide(db,rid,'org-two','pb-two',[{'customer_id':'customer-two','action':'approve'}],row.version)
        assert db.scalar(select(ContactTask)) is None


def test_worker_retries_transient_errors_but_not_missing_evidence(seeded_db,monkeypatch):
    from api.worker.analysis import AnalysisWorker,TransientAnalysisError
    from shared.config import get_settings
    monkeypatch.setenv('ANALYSIS_RETRY_DELAY_SECONDS','0');get_settings.cache_clear()
    prepare(score())
    calls=[]
    async def transient(context):calls.append(context);raise TransientAnalysisError('private upstream details')
    worker=AnalysisWorker(transient)
    for _ in range(4):asyncio.run(worker.run_once())
    assert len(calls)==3 and len({c.thread_id for c in calls})==1
    with db_module.get_session() as db:
        job=db.scalar(select(AnalysisJob));assert job.status=='FAILED' and job.error_code=='UPSTREAM_ERROR'
    get_settings.cache_clear()


def test_public_worker_finishes_without_private_graph_and_private_executor_is_scoped(seeded_db):
    from api.worker.analysis import AnalysisWorker
    from api.review_executor import run_review
    result=score();prepare(result)
    contexts=[]
    async def public(context):
        contexts.append(context)
        return {'status':'DONE','score_result':result,'ai_explanation':'감사 공시 근거를 확인해 주세요.'}
    aid=asyncio.run(AnalysisWorker(public).run_once())
    with db_module.get_session() as db:
        review=db.scalar(select(OrganizationReview).where(OrganizationReview.assessment_id==aid))
        rid,tenant=review.review_id,review.tenant_id
    private_contexts=[]
    async def drafts(context):
        private_contexts.append(context)
        assert context.tenant_id==tenant and context.thread_id.startswith('review:')
        return {'drafts':{c['customer_id']:'{고객명}님, 공시 내용을 확인하시고 PB와 상담해 주세요.' for c in context.customers}}
    asyncio.run(run_review(drafts,rid,tenant))
    assert len(contexts)==len(private_contexts)==1
    with db_module.get_session() as db:
        assert db.get(OrganizationReview,rid).status=='WAITING_APPROVAL'
        assert db.get(AnalysisJob,contexts[0].job_id).status=='DONE'


def test_manual_evidence_failure_is_not_automatically_retried(seeded_db):
    from api.worker.analysis import AnalysisWorker
    result=score();prepare(result)
    calls=[]
    async def missing(context):calls.append(context);return {'status':'MANUAL_CHECK'}
    worker=AnalysisWorker(missing)
    asyncio.run(worker.run_once());asyncio.run(worker.run_once())
    with db_module.get_session() as db:
        job=db.scalar(select(AnalysisJob))
        assert job.status=='MANUAL_CHECK' and job.attempts==1
        assert db.get(Disclosure,RCEPT).processed_at is None
    assert len(calls)==1


def test_explicit_reanalysis_has_a_new_idempotent_request_version(seeded_db):
    from api.worker.analysis import AnalysisWorker
    result=score();original=prepare(result)
    async def missing(context):return {'status':'MANUAL_CHECK'}
    asyncio.run(AnalysisWorker(missing).run_once())
    with db_module.get_session() as db:
        a=enqueue(db,RCEPT,DAY,result['rules_digest'],request_version='explicit-request-fixture')
        b=enqueue(db,RCEPT,DAY,result['rules_digest'],request_version='explicit-request-fixture')
        assert a.job_id==b.job_id and a.job_id!=original
        assert db.get(AnalysisJob,original).status=='MANUAL_CHECK'


def test_executor_cannot_override_rule_scores(seeded_db):
    result=score();job_id=prepare(result)
    result['yellow_score']=999
    with db_module.get_session() as db:
        job=claim(db);token=job.lease_token
    with db_module.get_session() as db:
        with pytest.raises(ValueError,match='계산 도구'):
            complete_analysis(db,job_id,token,result,'공시를 확인해 주세요.')
        assert db.scalar(select(PublicAssessment)) is None
