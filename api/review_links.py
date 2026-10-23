"""Resolve only the current public decision and the requesting PB's review."""
from sqlalchemy import select
from shared.models import PublicAssessment, OrganizationReview, Customer
from risk_engine.stages import ALERT_STAGES


async def review_links(db,pb,state,assessment,adapter):
    links={'assessment_id':assessment.assessment_id if assessment else None,'public_assessment_id':None,
           'review_id':None,'review_status':None,'review_backend':None,
           'my_approval_pending':False,'can_review':False}
    owned=set(db.scalars(select(Customer.customer_id).where(Customer.tenant_id==pb.tenant_id,Customer.pb_id==pb.pb_id)))
    public=db.get(PublicAssessment,state.public_assessment_id) if state.public_assessment_id else None
    if public and public.state_version==state.state_version:
        links.update(assessment_id=public.legacy_assessment_id,public_assessment_id=public.assessment_id,
                     review_backend='organization')
        review=db.scalar(select(OrganizationReview).where(OrganizationReview.tenant_id==pb.tenant_id,
                         OrganizationReview.assessment_id==public.assessment_id))
        if review:
            links.update(review_id=review.review_id,review_status=review.status)
            if not state.resolved and state.stage in ALERT_STAGES and review.status=='WAITING_APPROVAL':
                pending=any(cid in owned and cid not in (review.decisions or {}) for cid in (review.drafts or {}))
                links.update(my_approval_pending=pending,can_review=pending)
        return links
    if not assessment or state.resolved or state.stage not in ALERT_STAGES:
        return links
    links.update(review_backend='legacy',review_status=assessment.status)
    if assessment.status!='WAITING_APPROVAL':return links
    links.update(my_approval_pending=None,can_review=None)
    if pb.tenant_id!='default':
        links['review_backend']='unsupported'
        return links
    # Read-only state lookup; never starts or resumes a Graph.
    try:view=await adapter.get_state(assessment.rcept_no)
    except Exception:
        links['review_status']='UNKNOWN'
        return links
    if not view or view.get('assessment_id')!=assessment.assessment_id:
        links['review_status']='UNKNOWN'
        return links
    if assessment.score_fingerprint and view.get('review_fingerprint')!=assessment.score_fingerprint:
        links.update(review_status='STALE',my_approval_pending=False,can_review=False)
        return links
    pending=view.get('status')=='WAITING_APPROVAL' and any(
        cid in owned and cid not in view.get('decided',{}) for cid in view.get('drafts',{}))
    links.update(review_status=view.get('status'),my_approval_pending=bool(pending),can_review=bool(pending))
    return links
