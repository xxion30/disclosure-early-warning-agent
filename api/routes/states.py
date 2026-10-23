"""PB current risk state API. Explicit public projections prevent score leakage."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, case
from sqlalchemy.orm import Session
from api.auth import CurrentPB, get_current_pb
from api.deps import get_db
from api.graph_adapter import GraphAdapter, get_graph_adapter
from api.review_links import review_links
from shared.display import STAGE_LABELS, missing_labels
from api.tenancy import visible_stock_codes
from shared.models import Company, Disclosure, RiskState, StateRead, RiskAssessment, CollectCursor, RiskStateChange
from shared.models.risk import STAGES
from risk_engine.stages import ALERT_STAGES
from risk_engine.loader import load_rules
from shared.presentation import public_explanation

router = APIRouter(prefix='/states', tags=['states'])


def public_item(item):
    # Whitelist, not a blacklist: scores can hide inside arbitrary detail/extras fields.
    return {key: item.get(key) for key in ('group', 'item', 'signal_type', 'event_id', 'rcept_no',
            'filed_at', 'active', 'resolved', 'missing')} | {
        'explanation': public_explanation(item.get('explanation')),
        'missing_labels': missing_labels(item.get('missing')),
        'fs_div': item.get('fs_div'),
        'basis_checks': [{key: basis.get(key) for key in
                          ('fs_div', 'period', 'rcept_no', 'revision', 'status', 'impairment_pct')}
                         for basis in item.get('basis_checks', []) if isinstance(basis, dict)],
        'evidence': [{key: e.get(key) for key in ('source', 'quote', 'rcept_no', 'location')}
                     for e in item.get('evidence', []) if isinstance(e, dict)]}


def visible_state(db, pb, stock_code):
    row = db.scalar(select(RiskState).where(RiskState.stock_code == stock_code, RiskState.stock_code.in_(visible_stock_codes(pb))).execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, '종목 위험 상태를 찾을 수 없습니다.')
    return row


def project(db, pb, row):
    company = db.get(Company, row.corp_code)
    read = db.get(StateRead, (pb.pb_id, row.corp_code))
    cursor = db.get(CollectCursor, row.stock_code)
    return {'stock_code': row.stock_code, 'corp_code': row.corp_code, 'corp_name': company.name,
            'stage': row.stage, 'stage_label': STAGE_LABELS.get(row.stage), 'active_stages': row.active_stages, 'manual_check': row.manual_check,
            'resolved': row.resolved, 'version': row.alert_version,
            'new': row.stage in ALERT_STAGES and row.alert_version > (read.read_version if read else 0),
            'updated_at': row.updated_at, 'latest_filed_at': row.latest_filed_at,
            'last_collection_at': cursor.last_success_at if cursor else None,
            'risk_types': sorted({i['signal_type'] for i in row.items if i.get('active')})}


@router.get('')
def list_states(stage: list[str] | None = Query(None), alert: bool = False,
                manual_check: bool | None = None, resolved: bool | None = None,
                limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                pb: CurrentPB = Depends(get_current_pb), db: Session = Depends(get_db)):
    if stage and any(value not in STAGES for value in stage):
        raise HTTPException(422, '유효하지 않은 stage입니다.')
    query = select(RiskState).where(RiskState.stock_code.in_(visible_stock_codes(pb)))
    if stage:
        query = query.where(RiskState.stage.in_(stage))
    if alert:
        query = query.where(RiskState.stage.in_(ALERT_STAGES), RiskState.resolved.is_(False))
    if manual_check is not None:
        query = query.where(RiskState.manual_check == manual_check)
    if resolved is not None:
        query = query.where(RiskState.resolved == resolved)
    rows = list(db.scalars(query))
    order = load_rules()['stage_priority']
    rows.sort(key=lambda r: r.stock_code)
    rows.sort(key=lambda r: r.latest_filed_at, reverse=True)
    rows.sort(key=lambda r: (order[r.stage], -r.sort_value))
    return {'items': [project(db, pb, r) for r in rows[offset:offset+limit]], 'total': len(rows), 'limit': limit, 'offset': offset}


@router.get('/{stock_code}')
async def state_detail(stock_code: str, pb: CurrentPB = Depends(get_current_pb), db: Session = Depends(get_db),
                       adapter: GraphAdapter = Depends(get_graph_adapter)):
    row = visible_state(db, pb, stock_code)
    timeline = db.scalars(select(Disclosure).where(Disclosure.corp_code == row.corp_code)
                         .order_by(Disclosure.filed_at.desc(), Disclosure.rcept_no.desc()))
    assessment = db.scalar(select(RiskAssessment).where(RiskAssessment.corp_code == row.corp_code, RiskAssessment.risk_state_version == row.state_version).order_by(RiskAssessment.created_at.desc(), RiskAssessment.assessment_id.desc()))
    change=db.scalar(select(RiskStateChange).where(RiskStateChange.corp_code==row.corp_code,
        RiskStateChange.state_version==row.state_version))
    links=await review_links(db,pb,row,assessment,adapter)
    return {**project(db, pb, row), **links,
            'comparison_available': change is not None and change.from_stage is not None,
            'changes': change.changes if change else [],
            'change_version': change.state_version if change else None,
            'changed_at': change.created_at if change else None,
            'ai_explanation': public_explanation(assessment.ai_explanation) if assessment else None, 'items': [public_item(i) for i in row.items],
            'resolution_evidence': [{k: e.get(k) for k in ('source', 'quote', 'rcept_no', 'location')} for e in row.resolution_evidence],
            'timeline': [{k: getattr(d, k) for k in ('rcept_no', 'title', 'filed_at', 'url', 'event_id')} for d in timeline]}


class Acknowledge(BaseModel):
    version: int = Field(ge=0)


@router.post('/{stock_code}/ack')
def acknowledge(stock_code: str, body: Acknowledge, pb: CurrentPB = Depends(get_current_pb), db: Session = Depends(get_db)):
    row = visible_state(db, pb, stock_code)
    if body.version > row.alert_version:
        raise HTTPException(409, '아직 존재하지 않는 알림 버전입니다.')
    dialect = db.get_bind().dialect.name
    if dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise HTTPException(503, '지원하지 않는 읽음 저장소입니다.')
    statement = insert(StateRead).values(pb_id=pb.pb_id, corp_code=row.corp_code,
                                        tenant_id=pb.tenant_id, read_version=body.version)
    statement = statement.on_conflict_do_update(index_elements=['pb_id', 'corp_code'],
        set_={'read_version': case((statement.excluded.read_version > StateRead.read_version,
                                   statement.excluded.read_version), else_=StateRead.read_version)})
    read_version = db.execute(statement.returning(StateRead.read_version)).scalar_one()
    return {'stock_code': stock_code, 'read_version': read_version,
            'new': row.alert_version > read_version and row.stage in ALERT_STAGES}
