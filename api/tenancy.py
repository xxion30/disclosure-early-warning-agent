"""Tenant/PB visibility predicates. Never accept tenant identity from request JSON."""
from fastapi import HTTPException
from sqlalchemy import select
from shared.models import Company, Customer, Holding, RiskAssessment


def visible_stock_codes(pb):
    return (select(Holding.stock_code).join(Customer, Customer.customer_id == Holding.customer_id)
            .where(Customer.pb_id == pb.pb_id, Customer.tenant_id == pb.tenant_id, Holding.tenant_id == pb.tenant_id))


def require_assessment_access(db, pb, assessment):
    company = db.get(Company, assessment.corp_code)
    if company is None or db.scalar(visible_stock_codes(pb).where(Holding.stock_code == company.stock_code).limit(1)) is None:
        raise HTTPException(404, '경보를 찾을 수 없습니다.')


def require_legacy_runtime(pb):
    from shared.config import get_settings
    if get_settings().PHASE1_RUNTIME_FACTORY:
        raise HTTPException(409, '현재 서비스는 /analysis/jobs와 조직별 /reviews 경로를 사용합니다.')
    # R2 runtime still keys checkpoints by rcept_no. No private execution across companies until migrated.
    if pb.tenant_id != 'default':
        raise HTTPException(503, '회사별 Agent 실행 연결이 필요합니다.')
