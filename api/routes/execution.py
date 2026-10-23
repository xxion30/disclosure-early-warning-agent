"""New execution API. Legacy rcept-only Graph paths stay separate."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from api.auth import CurrentPB, get_current_pb
from api.deps import get_db
from shared.models import Disclosure, AnalysisJob, PublicAssessment, Customer, Holding
from shared.analysis import enqueue
from mcp_servers.business.reviews import create_review, scoped_review, decide
from risk_engine.loader import load_rules
from shared.presentation import public_explanation
from api.review_executor import run_review
import hashlib
import json
from uuid import UUID

router=APIRouter(tags=['execution'])


class JobRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    rcept_no: str=Field(pattern=r'^\d{14}$')
    as_of: str
    reanalysis_request_id: UUID | None = None


def require_holding(db,pb,corp_code):
    from shared.models import Company
    stock=db.scalar(select(Company.stock_code).where(Company.corp_code==corp_code))
    if not db.scalar(select(Customer.customer_id).join(Holding,Holding.customer_id==Customer.customer_id).where(
        Customer.tenant_id==pb.tenant_id,Holding.tenant_id==pb.tenant_id,Holding.stock_code==stock,
        Customer.pb_id==pb.pb_id).limit(1)):
        # Admin can schedule for its organization, but no other organization's holdings.
        if pb.role!='ADMIN' or not db.scalar(select(Customer.customer_id).join(Holding,Holding.customer_id==Customer.customer_id).where(
            Customer.tenant_id==pb.tenant_id,Holding.tenant_id==pb.tenant_id,Holding.stock_code==stock).limit(1)):
            raise HTTPException(404,'접근 가능한 보유 종목이 아닙니다.')


@router.post('/analysis/jobs',status_code=202)
def create_job(body:JobRequest,pb:CurrentPB=Depends(get_current_pb),db=Depends(get_db)):
    if pb.role!='ADMIN':raise HTTPException(403,'관리자만 분석을 요청할 수 있습니다.')
    disc=db.get(Disclosure,body.rcept_no)
    if disc is None:raise HTTPException(404,'공시를 찾을 수 없습니다.')
    require_holding(db,pb,disc.corp_code)
    digest=hashlib.sha256(json.dumps(load_rules(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    try:job=enqueue(db,body.rcept_no,body.as_of,digest,request_version=body.reanalysis_request_id)
    except ValueError as exc:raise HTTPException(409,str(exc)) from None
    return {'job_id':job.job_id,'status':job.status}


@router.get('/analysis/jobs/{job_id}')
def job_status(job_id:str,pb:CurrentPB=Depends(get_current_pb),db=Depends(get_db)):
    row=db.get(AnalysisJob,job_id)
    if row is None:raise HTTPException(404,'작업을 찾을 수 없습니다.')
    require_holding(db,pb,row.corp_code)
    assessment=db.scalar(select(PublicAssessment).where(PublicAssessment.job_id==job_id))
    return {'job_id':job_id,'status':row.status,'attempts':row.attempts,'error_code':row.error_code,
            'public_assessment_id':assessment.assessment_id if assessment else None,
            'assessment_id':assessment.legacy_assessment_id if assessment else None}


@router.get('/analysis/jobs')
def analysis_jobs(pb:CurrentPB=Depends(get_current_pb),db=Depends(get_db)):
    from shared.models import Company
    from api.tenancy import visible_stock_codes
    rows = db.execute(select(AnalysisJob, Company).join(Company, Company.corp_code == AnalysisJob.corp_code)
        .where(Company.stock_code.in_(visible_stock_codes(pb)))
        .order_by(AnalysisJob.created_at.desc()).limit(50)).all()
    return {'items': [{'job_id': j.job_id, 'rcept_no': j.rcept_no, 'status': j.status,
                       'attempts': j.attempts, 'error_code': j.error_code, 'corp_name': c.name,
                       'stock_code': c.stock_code, 'created_at': j.created_at} for j,c in rows]}


@router.post('/public-assessments/{assessment_id}/reviews',status_code=201)
def make_review(assessment_id:str,pb:CurrentPB=Depends(get_current_pb),db=Depends(get_db)):
    assessment=db.get(PublicAssessment,assessment_id)
    if assessment is None:raise HTTPException(404,'판정을 찾을 수 없습니다.')
    require_holding(db,pb,assessment.corp_code)
    try:row=create_review(db,pb.tenant_id,assessment_id)
    except (ValueError,PermissionError) as exc:raise HTTPException(409,str(exc)) from None
    return {'review_id':row.review_id,'status':row.status,'version':row.version}


@router.get('/reviews/{review_id}')
def get_review(review_id:str,pb:CurrentPB=Depends(get_current_pb),db=Depends(get_db)):
    try:row=scoped_review(db,review_id,pb.tenant_id)
    except PermissionError:raise HTTPException(404,'검토를 찾을 수 없습니다.') from None
    assessment=db.get(PublicAssessment,row.assessment_id)
    require_holding(db,pb,assessment.corp_code)
    from harness.guardrails import check_message
    allowed=set(db.scalars(select(Customer.customer_id).where(Customer.tenant_id==pb.tenant_id,Customer.pb_id==pb.pb_id)))
    return {'review_id':row.review_id,'assessment_id':row.assessment_id,'status':row.status,'version':row.version,
            'stage':assessment.result['stage'],'ai_explanation':public_explanation(assessment.explanation),
            'drafts':{cid:('' if check_message(text) else text) for cid,text in row.drafts.items() if cid in allowed},
            'decisions':{cid:d for cid,d in row.decisions.items() if cid in allowed}}


@router.post('/reviews/{review_id}/run')
async def generate_review(review_id:str,request:Request,pb:CurrentPB=Depends(get_current_pb),db=Depends(get_db)):
    try:row=scoped_review(db,review_id,pb.tenant_id)
    except PermissionError:raise HTTPException(404,'검토를 찾을 수 없습니다.') from None
    assessment=db.get(PublicAssessment,row.assessment_id);require_holding(db,pb,assessment.corp_code)
    executor=getattr(request.app.state,'review_executor',None)
    if executor is None:raise HTTPException(503,'R2 조직별 초안 executor 연결이 필요합니다.')
    db.commit()  # independent executor transactions must not inherit request locks
    try:await run_review(executor,review_id,pb.tenant_id)
    except (ValueError,PermissionError):raise HTTPException(409,'초안 또는 최신 판정을 확인해야 합니다.') from None
    except Exception:raise HTTPException(503,'초안 실행을 완료하지 못했습니다. 실행 상태를 확인해 주세요.') from None
    return {'review_id':review_id,'status':'WAITING_APPROVAL'}


class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    customer_id:str
    action:str
    edited_draft:str|None=None
    reject_reason:str|None=None


class Decisions(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version:int=Field(ge=0)
    decisions:list[Decision]=Field(min_length=1)


@router.post('/reviews/{review_id}/decisions')
def review_decisions(review_id:str,body:Decisions,pb:CurrentPB=Depends(get_current_pb),db=Depends(get_db)):
    try:row=decide(db,review_id,pb.tenant_id,pb.pb_id,[d.model_dump(exclude_none=True) for d in body.decisions],body.expected_version)
    except PermissionError as exc:raise HTTPException(403,str(exc)) from None
    except ValueError as exc:raise HTTPException(409,str(exc)) from None
    return {'review_id':row.review_id,'status':row.status,'version':row.version}
