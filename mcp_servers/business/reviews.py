"""Organization-scoped review persistence and task writes. No Graph implementation."""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from shared.models import (OrganizationReview, PublicAssessment, RiskState, Tenant, Customer, Holding,
                           PB, ContactTask, AuditLog, Company)
from shared.analysis import utcnow, LostLease
from shared.config import get_settings

ALERT_STAGES={'RED','ORANGE','YELLOW','COMPOSITE'}


def lock_current(db, assessment):
    db.scalar(select(Company).where(Company.corp_code==assessment.corp_code).with_for_update())
    state=db.scalar(select(RiskState).where(RiskState.corp_code==assessment.corp_code).with_for_update()
                    .execution_options(populate_existing=True))
    if state is None or state.public_assessment_id!=assessment.assessment_id or state.state_version!=assessment.state_version or state.resolved or state.stage not in ALERT_STAGES:
        raise ValueError('최신 경보 판정이 아닙니다. 새 검토가 필요합니다.')
    return state


def create_review(db, tenant_id, assessment_id):
    assessment=db.get(PublicAssessment,assessment_id)
    if assessment is None or db.get(Tenant,tenant_id) is None:raise ValueError('판정 또는 이용 조직이 없습니다.')
    state=lock_current(db,assessment)
    if not db.scalar(select(Customer.customer_id).join(Holding,Holding.customer_id==Customer.customer_id).where(
        Customer.tenant_id==tenant_id,Holding.tenant_id==tenant_id,Holding.stock_code==state.stock_code).limit(1)):
        raise PermissionError('해당 조직의 보유 고객이 없습니다.')
    existing=db.scalar(select(OrganizationReview).where(OrganizationReview.tenant_id==tenant_id,
                       OrganizationReview.assessment_id==assessment_id))
    if existing:return existing
    try:
        with db.begin_nested():
            key=str(uuid4())
            row=OrganizationReview(review_id=key,tenant_id=tenant_id,assessment_id=assessment_id,
                thread_id='review:'+key,status='PENDING',drafts={},decisions={},rewrite_customers=[],version=0,created_at=utcnow().isoformat())
            db.add(row);db.flush()
        return row
    except IntegrityError:
        return db.scalar(select(OrganizationReview).where(OrganizationReview.tenant_id==tenant_id,
                         OrganizationReview.assessment_id==assessment_id))


def scoped_review(db, review_id, tenant_id, *, lock=False):
    query=select(OrganizationReview).where(OrganizationReview.review_id==review_id,OrganizationReview.tenant_id==tenant_id)
    if lock:query=query.with_for_update()
    row=db.scalar(query.execution_options(populate_existing=True))
    if row is None:raise PermissionError('해당 조직의 검토가 아닙니다.')
    return row


def claim_review(db, review_id, tenant_id, *, now=None):
    now=now or utcnow()
    # Public-state -> review lock order matches invalidation on public publication.
    visible=scoped_review(db,review_id,tenant_id)
    assessment=db.get(PublicAssessment,visible.assessment_id);lock_current(db,assessment)
    row=scoped_review(db,review_id,tenant_id,lock=True)
    if row.status not in ('PENDING','MANUAL_CHECK','RUNNING'):
        raise ValueError('초안 생성 가능한 상태가 아닙니다.')
    if row.status=='RUNNING' and row.lease_expires_at and row.lease_expires_at>now.isoformat():
        raise ValueError('이미 검토를 실행 중입니다.')
    row.status='RUNNING';row.lease_token=str(uuid4())
    row.lease_expires_at=(now+timedelta(seconds=get_settings().ANALYSIS_LEASE_SECONDS)).isoformat()
    row.version+=1;db.flush()
    return row


def save_drafts(db, review_id, tenant_id, token, drafts, *, now=None):
    from harness.guardrails import check_message
    now=now or utcnow()
    row=scoped_review(db,review_id,tenant_id)
    assessment=db.get(PublicAssessment,row.assessment_id);state=lock_current(db,assessment)
    row=scoped_review(db,review_id,tenant_id,lock=True)
    if row.status!='RUNNING' or row.lease_token!=token or not row.lease_expires_at or row.lease_expires_at<=now.isoformat():
        raise LostLease('검토 실행 임대가 만료됐습니다.')
    customers=set(db.scalars(select(Customer.customer_id).join(Holding,Holding.customer_id==Customer.customer_id).where(
        Customer.tenant_id==tenant_id,Holding.tenant_id==tenant_id,Holding.stock_code==state.stock_code)))
    if not isinstance(drafts,dict) or set(drafts)!=customers or any(not isinstance(v,str) or not v.strip() or check_message(v) for v in drafts.values()):
        raise ValueError('보유 고객 전원의 검증된 안내문이 필요합니다.')
    # Preserve messages already approved/rejected in previous PB batches.
    row.drafts={cid: row.drafts[cid] if cid in (row.decisions or {}) else text for cid,text in drafts.items()}
    row.rewrite_customers=[];row.status='WAITING_APPROVAL';row.lease_token=row.lease_expires_at=None;row.version+=1
    db.flush();return row


def decide(db, review_id, tenant_id, pb_id, decisions, expected_version):
    """Authenticated PB decision and idempotent task creation in one transaction."""
    from harness.guardrails import check_message
    visible=scoped_review(db,review_id,tenant_id)
    assessment=db.get(PublicAssessment,visible.assessment_id)
    # Reject-only can close old drafts, but creates no tasks.
    if any(d['action']!='reject' for d in decisions):lock_current(db,assessment)
    row=scoped_review(db,review_id,tenant_id,lock=True)
    pb=db.get(PB,pb_id)
    if pb is None or pb.tenant_id!=tenant_id:raise PermissionError('PB 소속이 다릅니다.')
    ids=[d['customer_id'] for d in decisions]
    if not ids or len(ids)!=len(set(ids)):raise ValueError('중복 없는 고객 결정이 필요합니다.')
    owners={c.customer_id:c.pb_id for c in db.scalars(select(Customer).where(
        Customer.tenant_id==tenant_id,Customer.customer_id.in_(ids)))}
    if any(owners.get(cid)!=pb_id or cid not in row.drafts for cid in ids):raise PermissionError('담당 고객만 결정할 수 있습니다.')
    stored=dict(row.decisions or {})
    incoming={d['customer_id']:{**d,'pb_id':pb_id} for d in decisions}
    if all(stored.get(cid)==value for cid,value in incoming.items()):return row
    if any(cid in stored for cid in ids):raise ValueError('이미 처리된 결정입니다.')
    if row.version!=expected_version:raise ValueError('검토 버전이 변경됐습니다.')
    reject_only=all(d['action']=='reject' for d in decisions)
    if row.status!='WAITING_APPROVAL' and not (reject_only and row.status in ('SUPERSEDED','RESOLVED')):
        raise ValueError('승인 대기 상태가 아닙니다.')
    for decision in decisions:
        cid=decision['customer_id'];action=decision['action']
        if action not in ('approve','reject','rewrite'):raise ValueError('허용되지 않은 결정입니다.')
        if action=='reject' and not (decision.get('reject_reason') or '').strip():raise ValueError('반려 사유가 필요합니다.')
        if action=='approve':
            message=decision.get('edited_draft') or row.drafts[cid]
            if check_message(message) or not message.strip():raise ValueError('검사 실패 문안은 승인할 수 없습니다.')
            db.add(ContactTask(review_id=review_id,assessment_id=assessment.legacy_assessment_id,
                tenant_id=tenant_id,customer_id=cid,pb_id=pb_id,draft_message=message,status='승인',
                created_at=utcnow().isoformat(),updated_at=utcnow().isoformat()))
        if action=='rewrite':
            row.rewrite_customers=sorted(set([*(row.rewrite_customers or []),cid]))
            row.status='PENDING'
        else:
            stored[cid]=incoming[cid]
        db.add(AuditLog(tenant_id=tenant_id,actor=pb_id,action='review_decision',target=review_id,
                       payload={'customer_id':cid,'action':action,'version':row.version},created_at=utcnow().isoformat()))
    row.decisions=stored;row.version+=1
    if set(stored)==set(row.drafts):
        row.status='DONE';row.finished_at=utcnow().isoformat()
    db.flush();return row
