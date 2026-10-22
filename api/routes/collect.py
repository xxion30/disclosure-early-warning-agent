"""Collection dispatch boundary. Real collector implementation is owned by R3."""
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from api.auth import CurrentPB, get_current_pb
from api.deps import get_db
from shared.collection import reserve_stocks, release_run
from shared.models import CollectRun, Company, Customer, Holding

router = APIRouter(prefix='/collect', tags=['collect'])


def run_view(row):
    return {key: getattr(row, key) for key in ('run_id', 'status', 'started_at', 'finished_at', 'fetched_count', 'failed_count', 'error')}


@router.get('/status')
def collect_status(pb: CurrentPB = Depends(get_current_pb), db: Session = Depends(get_db)):
    # Global jobs expose aggregate progress only; never their watchlist or requester identity.
    rows = db.scalars(select(CollectRun).where(or_(CollectRun.tenant_id == pb.tenant_id, CollectRun.tenant_id.is_(None)))
                      .order_by(CollectRun.started_at.desc()).limit(20))
    return {'runs': [run_view(r) for r in rows]}


@router.post('/run', status_code=202)
async def collect_run(request: Request, pb: CurrentPB = Depends(get_current_pb), db: Session = Depends(get_db)):
    if pb.role != 'ADMIN':
        raise HTTPException(403, '회사 관리자만 수동 수집할 수 있습니다.')
    dispatch = getattr(request.app.state, 'collect_dispatcher', None)
    if dispatch is None:
        raise HTTPException(503, 'R3 수집기 실행 연결이 필요합니다.')
    # Serialize requests for one tenant without adding a queue.
    from shared.models import Tenant
    db.scalar(select(Tenant).where(Tenant.tenant_id == pb.tenant_id).with_for_update())
    stocks = sorted(set(db.scalars(select(Holding.stock_code).join(Customer, Customer.customer_id == Holding.customer_id)
                                  .join(Company, Company.stock_code == Holding.stock_code)
                                  .where(Holding.tenant_id == pb.tenant_id, Customer.tenant_id == pb.tenant_id))))
    if not stocks:
        raise HTTPException(409, '수집할 보유 종목이 없습니다.')
    existing = db.scalar(select(CollectRun).where(CollectRun.tenant_id == pb.tenant_id, CollectRun.status.in_(['PENDING', 'RUNNING'])))
    if existing:
        return run_view(existing)
    row = CollectRun(run_id=str(uuid4()), tenant_id=pb.tenant_id, requested_by=pb.pb_id,
                     stock_codes=stocks, status='PENDING', started_at=datetime.now(timezone.utc).isoformat())
    db.add(row)
    db.flush()
    reserved = reserve_stocks(db, row.run_id, stocks)
    if not reserved:
        db.rollback()
        raise HTTPException(409, '해당 종목은 이미 수집 중입니다.')
    row.stock_codes = reserved
    db.commit()  # dispatcher uses its own transaction and must see the run
    try:
        await dispatch(row.run_id, reserved)
    except Exception:
        release_run(db, row.run_id)
        row.status = 'FAILED'
        row.finished_at = datetime.now(timezone.utc).isoformat()
        row.error = '수집기 작업 전달 실패'
        db.commit()
        raise HTTPException(503, row.error) from None
    return run_view(row)
