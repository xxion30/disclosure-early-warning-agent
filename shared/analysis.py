"""Durable handoff/leases. No Agent, collector, or scoring imports below shared."""
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased
from shared.models import Disclosure, FinancialSnapshot, Signal, AnalysisJob, Company, RiskState, PublicAssessment
from shared.facts import facts_hash
from shared.config import get_settings

JOB_STATUSES = ('PENDING','RUNNING','RETRY_WAIT','DONE','MANUAL_CHECK','FAILED')
MAX_ATTEMPTS = 3


def utcnow():
    return datetime.now(timezone.utc)


def input_manifest(db, corp_code, as_of, *, exclude_rcept=None):
    """Only public inputs available by the evaluation date; excludes document bodies."""
    end = str(as_of)[:10] + '~'
    disclosures = db.scalars(select(Disclosure).where(Disclosure.corp_code == corp_code, Disclosure.filed_at <= end)
                             .order_by(Disclosure.filed_at, Disclosure.rcept_no)).all()
    receipts = {d.rcept_no for d in disclosures}
    snapshots = db.scalars(select(FinancialSnapshot).where(FinancialSnapshot.corp_code == corp_code)
                           .order_by(FinancialSnapshot.id)).all()
    signals = db.scalars(select(Signal).where(Signal.corp_code == corp_code, Signal.filed_at <= end)
                         .order_by(Signal.signal_id)).all()
    return {'disclosures': [{'rcept_no':d.rcept_no,'title':d.title,'filed_at':d.filed_at,
                             'event_id':d.event_id,'original_rcept_no':d.original_rcept_no,
                             'event_started_at':d.event_started_at,'content_hash':d.content_hash,
                             'structured_facts':d.structured_facts} for d in disclosures],
            'snapshots': [{'rcept_no':v.rcept_no,'period':v.period,'period_kind':v.period_kind,'basis':v.basis,
                           'revision':v.revision,'indicators':v.indicators,'sources':v.sources}
                          for v in snapshots if v.rcept_no in receipts],
            'signals': [{'rcept_no':v.rcept_no,'event_id':v.event_id,'event_started_at':v.event_started_at,
                         'signal_type':v.signal_type,'detail':v.detail,'filed_at':v.filed_at} for v in signals if v.rcept_no != exclude_rcept]}


def mark_ready(db, rcept_no, *, now=None):
    """R3 calls after facts/snapshots (including known missing data) are committed in this transaction."""
    row = db.scalar(select(Disclosure).where(Disclosure.rcept_no == rcept_no).with_for_update())
    if row is None:
        raise ValueError('공시를 찾을 수 없습니다.')
    version=facts_hash(input_manifest(db,row.corp_code,row.filed_at[:10],exclude_rcept=rcept_no))
    if row.input_version != version:row.processed=False
    row.input_version=version
    row.analysis_ready_at = (now or utcnow()).isoformat()
    db.flush()
    return row.input_version


def enqueue(db, rcept_no, as_of, rules_digest, *, now=None, request_version=None):
    from datetime import date
    as_of = date.fromisoformat(as_of).isoformat()
    disc = db.get(Disclosure, rcept_no)
    if disc is None or disc.analysis_ready_at is None:
        raise ValueError('공시별 필수 수집 완료 인계가 필요합니다.')
    if disc.filed_at[:10] > as_of:
        raise ValueError('기준일 이후 공시는 분석할 수 없습니다.')
    manifest = input_manifest(db, disc.corp_code, as_of, exclude_rcept=rcept_no)
    if request_version is not None:manifest['_request_version']=str(request_version)
    digest = facts_hash(manifest)
    key = facts_hash([disc.corp_code, rcept_no, as_of, digest, rules_digest])
    existing = db.scalar(select(AnalysisJob).where(AnalysisJob.job_key == key))
    if existing:
        return existing
    try:
        with db.begin_nested():
            row = AnalysisJob(job_id=str(uuid4()),job_key=key,corp_code=disc.corp_code,rcept_no=rcept_no,
                              as_of=as_of,input_manifest=manifest,input_hash=digest,rules_digest=rules_digest,
                              status='PENDING',attempts=0,created_at=(now or utcnow()).isoformat())
            db.add(row);db.flush()
        return row
    except IntegrityError:
        return db.scalar(select(AnalysisJob).where(AnalysisJob.job_key == key))


def recover_expired(db, *, now=None):
    now = now or utcnow()
    rows = db.scalars(select(AnalysisJob).where(AnalysisJob.status=='RUNNING',AnalysisJob.lease_expires_at<=now.isoformat())
                       .with_for_update(skip_locked=True).execution_options(populate_existing=True)).all()
    for row in rows:
        row.status = 'FAILED' if row.attempts >= MAX_ATTEMPTS else 'RETRY_WAIT'
        row.error_code = 'LEASE_EXPIRED'
        row.next_attempt_at = now.isoformat()
        row.lease_token = row.lease_expires_at = None
        if row.status == 'FAILED':
            row.finished_at=now.isoformat()
            if facts_hash(current_manifest(db,row))==row.input_hash:
                db.get(Disclosure,row.rcept_no).processed=True
    db.flush()
    return len(rows)


def claim(db, *, now=None):
    now = now or utcnow()
    recover_expired(db,now=now)
    eligible = (AnalysisJob.status.in_(['PENDING','RETRY_WAIT']), AnalysisJob.attempts<MAX_ATTEMPTS,
                (AnalysisJob.next_attempt_at.is_(None) | (AnalysisJob.next_attempt_at<=now.isoformat())))
    rows = db.scalars(select(AnalysisJob).where(*eligible).order_by(AnalysisJob.created_at)
                       .with_for_update(skip_locked=True).limit(20)).all()
    for row in rows:
        # Serialize different jobs for one issuer as their signal histories are
        # dependent. PostgreSQL workers skip an issuer another claimant owns.
        company = db.scalar(select(Company).where(Company.corp_code == row.corp_code)
                            .with_for_update(skip_locked=True))
        if company is None or db.scalar(select(AnalysisJob.job_id).where(
                AnalysisJob.corp_code == row.corp_code, AnalysisJob.status == 'RUNNING',
                AnalysisJob.job_id != row.job_id).limit(1)):
            continue
        token = str(uuid4())
        other = aliased(AnalysisJob)
        busy_issuer = select(other.job_id).where(other.corp_code == row.corp_code,
            other.status == 'RUNNING', other.job_id != row.job_id).exists()
        changed = db.execute(update(AnalysisJob).where(AnalysisJob.job_id==row.job_id,*eligible,
            ~busy_issuer).values(
            status='RUNNING',attempts=AnalysisJob.attempts+1,lease_token=token,
            lease_expires_at=(now+timedelta(seconds=get_settings().ANALYSIS_LEASE_SECONDS)).isoformat()))
        if changed.rowcount:
            db.flush();db.refresh(row)
            return row
    return None


class LostLease(ValueError):
    pass


def locked_job(db, job_id, token, *, now=None):
    now = now or utcnow()
    row = db.scalar(select(AnalysisJob).where(AnalysisJob.job_id==job_id).with_for_update()
                    .execution_options(populate_existing=True))
    if row is None or row.status!='RUNNING' or row.lease_token!=token or not row.lease_expires_at or row.lease_expires_at<=now.isoformat():
        raise LostLease('분석 작업의 임대가 만료되거나 실행자가 변경됐습니다.')
    return row


def heartbeat(db, job_id, token, *, now=None):
    now = now or utcnow()
    changed = db.execute(update(AnalysisJob).where(AnalysisJob.job_id==job_id,AnalysisJob.status=='RUNNING',
        AnalysisJob.lease_token==token,AnalysisJob.lease_expires_at>now.isoformat()).values(
            lease_expires_at=(now+timedelta(seconds=get_settings().ANALYSIS_LEASE_SECONDS)).isoformat()))
    return changed.rowcount==1


def fail_job(db, job_id, token, code, *, retryable=False, manual=False, now=None):
    now = now or utcnow()
    row=locked_job(db,job_id,token,now=now)
    row.error_code=code  # no raw exception text, URLs, document bodies or credentials
    row.status='MANUAL_CHECK' if manual else 'RETRY_WAIT' if retryable and row.attempts<MAX_ATTEMPTS else 'FAILED'
    row.next_attempt_at=(now+timedelta(seconds=get_settings().ANALYSIS_RETRY_DELAY_SECONDS)).isoformat() if row.status=='RETRY_WAIT' else None
    row.finished_at=now.isoformat() if row.status in ('FAILED','MANUAL_CHECK') else None
    row.lease_token=row.lease_expires_at=None
    if row.status in ('FAILED','MANUAL_CHECK'):
        disc=db.get(Disclosure,row.rcept_no)
        # Do not swallow a newer collection handoff while closing an old input version.
        if facts_hash(current_manifest(db,row))==row.input_hash:
            disc.processed=True
    db.flush()
    return row.status


def enqueue_ready(db, as_of, rules_digest, *, limit=100):
    busy = set(db.scalars(select(AnalysisJob.corp_code).where(
        AnalysisJob.status.in_(('PENDING', 'RUNNING', 'RETRY_WAIT')))))
    rows=db.scalars(select(Disclosure).where(Disclosure.analysis_ready_at.is_not(None),
        Disclosure.processed.is_(False),Disclosure.filed_at<=as_of+'~',
        Disclosure.corp_code.not_in(busy)).order_by(Disclosure.filed_at,Disclosure.rcept_no)).yield_per(100)
    scheduled = []
    for row in rows:
        if row.corp_code in busy:
            continue
        # Build the next manifest only after the preceding filing publishes its
        # signals. Historical backfill cannot use future filings/snapshots.
        latest = db.scalar(select(Disclosure.rcept_no).where(
            Disclosure.corp_code == row.corp_code, Disclosure.analysis_ready_at.is_not(None),
            Disclosure.filed_at <= as_of + '~').order_by(Disclosure.filed_at.desc(), Disclosure.rcept_no.desc()).limit(1))
        evaluation_day = as_of if row.rcept_no == latest else row.filed_at[:10]
        scheduled.append(enqueue(db, row.rcept_no, evaluation_day, rules_digest))
        busy.add(row.corp_code)
        if len(scheduled) >= limit:
            break
    return scheduled


def enqueue_recalculations(db, as_of, rules_digest, *, limit=100):
    """Re-evaluate the latest filing when only time/rules changed; never mutate
    the current state behind its immutable public assessment or approval token.
    A failed/manual run for the same daily input is not silently retried forever.
    """
    busy = select(AnalysisJob.corp_code).where(AnalysisJob.status.in_(('PENDING','RUNNING','RETRY_WAIT')))
    unprocessed = select(Disclosure.corp_code).where(Disclosure.analysis_ready_at.is_not(None),
        Disclosure.processed.is_(False), Disclosure.filed_at <= as_of + '~')
    states = db.scalars(select(RiskState).where(RiskState.public_assessment_id.is_not(None),
        RiskState.corp_code.not_in(busy), RiskState.corp_code.not_in(unprocessed)).limit(limit)).all()
    jobs = []
    for state in states:
        receipt = db.scalar(select(Disclosure.rcept_no).where(Disclosure.corp_code == state.corp_code,
            Disclosure.analysis_ready_at.is_not(None), Disclosure.filed_at <= as_of + '~')
            .order_by(Disclosure.filed_at.desc(), Disclosure.rcept_no.desc()).limit(1))
        if receipt:
            public = db.get(PublicAssessment, state.public_assessment_id)
            previous = db.get(AnalysisJob, public.job_id) if public else None
            current = input_manifest(db, state.corp_code, as_of, exclude_rcept=receipt)
            pinned = ({k:v for k,v in previous.input_manifest.items() if k != '_request_version'}
                      if previous else None)
            if (state.as_of != as_of or state.rules_digest != rules_digest
                    or facts_hash(current) != facts_hash(pinned)):
                jobs.append(enqueue(db, receipt, as_of, rules_digest))
    return jobs


def current_manifest(db, job):
    manifest=input_manifest(db,job.corp_code,job.as_of,exclude_rcept=job.rcept_no)
    if '_request_version' in job.input_manifest:manifest['_request_version']=job.input_manifest['_request_version']
    return manifest
