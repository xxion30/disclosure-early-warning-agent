"""Run R2 draft generation with an immutable tenant/assessment context."""
from dataclasses import dataclass, field
from shared.db import get_session
from shared.models import PublicAssessment, RiskState, Customer, Holding
from mcp_servers.business.reviews import claim_review, save_drafts, scoped_review
from shared.analysis import utcnow, LostLease
from sqlalchemy import select
import asyncio


@dataclass(frozen=True)
class ReviewContext:
    review_id: str
    tenant_id: str
    assessment_id: str
    thread_id: str
    version: int
    public_result: dict
    explanation: str
    customers: tuple
    existing_drafts: dict
    decisions: dict
    rewrite_customers: tuple
    lease_token: str | None = field(default=None, repr=False)


async def run_review(executor, review_id, tenant_id, *, session_factory=get_session):
    if not callable(executor):raise RuntimeError('R2 조직별 초안 executor 연결이 필요합니다.')
    with session_factory() as db:
        row=claim_review(db,review_id,tenant_id)
        token=row.lease_token
        assessment=db.get(PublicAssessment,row.assessment_id)
        state=db.get(RiskState,assessment.corp_code)
        customers=db.execute(select(Customer.customer_id,Holding.eval_amount).join(Holding,Holding.customer_id==Customer.customer_id)
            .where(Customer.tenant_id==tenant_id,Holding.tenant_id==tenant_id,Holding.stock_code==state.stock_code)
            .order_by(Holding.eval_amount.desc(),Customer.customer_id)).all()
        context=ReviewContext(row.review_id,tenant_id,row.assessment_id,row.thread_id,row.version,
            assessment.result,assessment.explanation,tuple({'customer_id':cid,'eval_amount':amount} for cid,amount in customers),
            dict(row.drafts or {}),dict(row.decisions or {}),tuple(row.rewrite_customers or []),lease_token=token)
    try:
        # Bound private generation: approval waiting is outside this operation.
        from shared.config import get_settings
        from datetime import timedelta
        from sqlalchemy import update
        task=asyncio.create_task(executor(context))
        async def renew():
            while not task.done():
                await asyncio.sleep(max(1,get_settings().ANALYSIS_LEASE_SECONDS/3))
                now=utcnow()
                with session_factory() as db:
                    changed=db.execute(update(type(row)).where(type(row).review_id==review_id,
                        type(row).tenant_id==tenant_id,type(row).status=='RUNNING',type(row).lease_token==token,
                        type(row).lease_expires_at>now.isoformat()).values(
                        lease_expires_at=(now+timedelta(seconds=get_settings().ANALYSIS_LEASE_SECONDS)).isoformat()))
                    alive=changed.rowcount==1
                if not alive:
                    task.cancel();return
        keeper=asyncio.create_task(renew())
        try:
            from harness.loop_limits import MAX_SECONDS
            result=await asyncio.wait_for(task,timeout=MAX_SECONDS)
        finally:
            keeper.cancel()
            from contextlib import suppress
            with suppress(asyncio.CancelledError):await keeper
        with session_factory() as db:
            saved=save_drafts(db,review_id,tenant_id,token,result['drafts'])
            return saved.review_id
    except BaseException:
        with session_factory() as db:
            current=scoped_review(db,review_id,tenant_id,lock=True)
            if current.status=='RUNNING' and current.lease_token==token:
                current.status='MANUAL_CHECK';current.lease_token=current.lease_expires_at=None
        raise
