"""경보 목록·상세·안내문 검토 (CLAUDE.md 8장 /alerts, /alerts/:id, /alerts/:id/drafts).

- 경보 숫자(등급, 점수, 점수 내역, 상태)는 DB risk_assessments에서 읽는다.
- 영향 고객·안내문 초안은 Graph State(체크포인트)에서 읽는다.
- 고객 실명은 로그인한 PB의 담당 고객만 채운다. 다른 PB 고객은 "고객 C002"처럼 가린다.
- Graph(MCP 서버·모델)에 붙지 못해도 목록·상세는 DB 내용으로 계속 보여준다 (graph_error로 알림).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from api.auth import CurrentPB, get_current_pb
from api.deps import get_db
from shared.presentation import public_explanation
from shared.display import STAGE_LABELS, stage_fields
from risk_engine.loader import load_rules
from risk_engine.stages import sort_value
from api.tenancy import visible_stock_codes, require_assessment_access, require_legacy_runtime
from shared.models import Holding
from api.graph_adapter import GraphAdapter, get_graph_adapter, pending_customers
from shared.models import Company, Customer, Disclosure, RiskAssessment

router = APIRouter(prefix="/alerts", tags=["alerts"])

NAME_PLACEHOLDER = "{고객명}"
ALERT_LEVELS = ("High", "Critical")  # 경보 등급: 담당 PB 대시보드 알림과 안내문 대상 (5-4장)
SCORE_KEYS = ("point", "score", "weight")  # 화면에는 점수 숫자를 내리지 않는다 (서버 안 정렬에만 쓴다)
GRAPH_DOWN = "진행 엔진(Graph)에 연결할 수 없습니다. MCP 서버(8001·8002)와 .env의 API 키를 확인하세요."

log = logging.getLogger("api.alerts")


async def read_state(adapter: GraphAdapter, rcept_no: str) -> tuple[dict | None, str | None]:
    """(State, 오류 문구). 진짜 모드에서 MCP 서버가 꺼져 Dependencies 조립이 실패해도 예외를 올리지 않는다."""
    try:
        return await adapter.get_state(rcept_no), None
    except Exception as exc:
        log.warning("Graph 상태 읽기 실패 %s: %s: %s", rcept_no, type(exc).__name__, exc)
        return None, GRAPH_DOWN


def masked_name(customer_id: str) -> str:
    return f"고객 {customer_id}"


def fill_name(text: str, name: str) -> str:
    return text.replace(NAME_PLACEHOLDER, name)


def get_assessment(db: Session, assessment_id: int) -> RiskAssessment:
    row = db.get(RiskAssessment, assessment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "경보를 찾을 수 없습니다.")
    return row


LEVEL_ORDER = ("Low", "Medium", "High", "Critical")


def strip_scores(rows: list | None) -> list:
    """점수 내역에서 점수 숫자 필드를 지운다. 순서는 점수 높은 순으로 먼저 맞춘다."""
    items = [r for r in (rows or []) if isinstance(r, dict)]
    items.sort(key=lambda r: -float(r.get("points") or 0))
    return [_strip(r) for r in items]


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if not any(w in k.lower() for w in SCORE_KEYS)}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def priority_key(a: RiskAssessment) -> tuple:
    rules = load_rules()
    if a.stage and a.stage != 'NONE':
        orange = sum(float(r.get('points') or 0) for r in (a.reasons or []) if r.get('group') == 'ORANGE')
        yellow = sum(float(r.get('points') or 0) for r in (a.reasons or []) if r.get('group') == 'YELLOW')
        # Proper new assessments have a full breakdown; retain deterministic ordering for minimal imported rows.
        if not a.reasons:
            orange = a.total_score if a.stage == 'ORANGE' else 0
            yellow = a.total_score if a.stage == 'YELLOW' else 0
        ratio = orange / rules['thresholds']['orange'] + yellow / rules['thresholds']['yellow']
        return (rules['stage_priority'][a.stage], -sort_value(a.stage, orange, yellow, ratio, rules))
    legacy = {'Critical': 'RED', 'High': 'ORANGE', 'Medium': 'WATCH', 'Low': 'NONE'}
    return (rules['stage_priority'][legacy.get(a.level, 'NONE')], -(a.total_score or 0))


def impact_summary(a: RiskAssessment, previous: RiskAssessment | None, customers: list[dict] | None) -> dict:
    """경보 상단용 '이번 공시의 영향도' 요약. AI 문장이 아니라 저장된 점수 내역과 영향 고객에서 규칙으로 만든다.

    - this_filing: 이번 공시(rcept_no)가 올린 위험 항목 (점수 숫자는 넣지 않는다)
    - previous: 같은 회사의 직전 평가 등급
    - customers: 보유 고객 수와 내가 아직 결정하지 않은 수. Graph에 붙지 못하면 None
    """
    rows = [r for r in (a.reasons or []) if isinstance(r, dict)]
    mine = [r for r in rows if r.get("rcept_no") == a.rcept_no and not r.get("from_history") and r.get("group") in ("A", "B", "RED", "ORANGE", "YELLOW") and r.get("active", True)]
    items = []
    for r in mine:
        quote = r.get("quote") or next((e.get("quote") for e in r.get("evidence") or [] if isinstance(e, dict)), None)
        items.append({
            "item": r.get("item") or r.get("signal_type"), "signal_type": r.get("signal_type"), "group": r.get("group"),
            "_points": float(r.get("points") or 0), "fixed_level": r.get("fixed_level"),
            "quote": (quote[:140] + "…") if quote and len(quote) > 140 else quote,
        })
    this_points = sum(i["_points"] for i in items)
    for i in items:
        i.pop("_points")
    fixed = [i["fixed_level"] for i in items if i["fixed_level"]]
    fixed_level = max(fixed, key=LEVEL_ORDER.index) if fixed else None
    prev_ok = previous is not None

    if fixed_level:
        headline = f"확정 악재({'·'.join(i['item'] for i in items if i['fixed_level'])})로 등급이 {fixed_level}로 고정됐습니다."
    elif items and this_points > 0:
        headline = f"이번 공시로 숨은 신호 {len(items)}건이 추가됐습니다."
    else:
        headline = "이번 공시로 추가된 위험 신호는 없습니다. 기존 신호 누적으로 등급이 유지됩니다."
    if prev_ok and previous.level != a.level:
        headline += f" 등급 {previous.level} → {a.level}."

    cust = None
    if customers is not None:
        cust = {"total": len(customers),
                "my_pending": sum(1 for c in customers if c.get("mine") and c.get("decision") == "승인대기")}
    if stage_fields(a)['stage_available']:
        names = '·'.join(i['item'] for i in items)
        headline = f'이번 공시의 위험 항목: {names}.' if names else '이번 공시의 추가 위험 항목은 없습니다.'
        headline += f' 현재 단계는 {STAGE_LABELS[a.stage]}입니다.'
    return {
        "headline": headline,
        "previous": {"assessment_id": previous.assessment_id, "level": previous.level} if prev_ok else None,
        "this_filing": {"items": items, "fixed_level": fixed_level},
        "customers": cust,
    }


def decision_status(view: dict | None, customer_id: str) -> str:
    """화면 표시용: 승인대기 / 승인 / 반려."""
    d = (view or {}).get("decided", {}).get(customer_id)
    if d is None:
        return "승인대기"
    return {"approve": "승인", "reject": "반려"}.get(d.get("action"), "승인대기")


def _customers(db: Session, ids: list[str]) -> dict[str, Customer]:
    if not ids:
        return {}
    return {c.customer_id: c for c in db.scalars(select(Customer).where(Customer.customer_id.in_(ids)))}


@router.get("")
async def list_alerts(
    level: list[str] | None = Query(None, description="여러 개 가능: ?level=High&level=Critical"),
    status_: str | None = Query(None, alias="status"),
    alert: bool = Query(False, description="true면 경보 등급(High·Critical)만 — 메인 대시보드 알림용"),
    date: str | None = Query(None, description="YYYY-MM-DD. 공시일 또는 생성일 (일일 요약 탭)"),
    pb: CurrentPB = Depends(get_current_pb),
    db: Session = Depends(get_db),
    adapter: GraphAdapter = Depends(get_graph_adapter),
) -> list[dict]:
    q = (
        select(RiskAssessment, Company.name, Disclosure.title, Disclosure.filed_at)
        .outerjoin(Company, Company.corp_code == RiskAssessment.corp_code)
        .outerjoin(Disclosure, Disclosure.rcept_no == RiskAssessment.rcept_no)
        .order_by(RiskAssessment.created_at.desc(), RiskAssessment.assessment_id.desc())
    )
    q = q.where(Company.stock_code.in_(visible_stock_codes(pb)))
    if level:
        q = q.where(RiskAssessment.level.in_(level))
    if alert:
        q = q.where(RiskAssessment.level.in_(ALERT_LEVELS), RiskAssessment.status != "FAILED")
    if status_:
        q = q.where(RiskAssessment.status == status_)
    if date:
        q = q.where(or_(Disclosure.filed_at.startswith(date), RiskAssessment.created_at.startswith(date)))

    rows = sorted(db.execute(q).all(), key=lambda r: priority_key(r[0]))  # 등급 → 점수 순 (최근 순은 쿼리 순서가 유지됨)
    my_ids = set(db.scalars(select(Customer.customer_id).where(Customer.pb_id == pb.pb_id, Customer.tenant_id == pb.tenant_id)))
    result = []
    graph_error = None  # 한 번 실패하면 남은 행은 다시 붙어 보지 않는다 (목록이 느려지지 않게)
    for a, corp_name, title, filed_at in rows:
        view = None
        if a.level in ALERT_LEVELS and graph_error is None and pb.tenant_id == "default":
            view, graph_error = await read_state(adapter, a.rcept_no)
        customers = (view or {}).get("affected_customers", [])
        result.append({
            "assessment_id": a.assessment_id,
            "rcept_no": a.rcept_no,
            "corp_name": corp_name or (view or {}).get("disclosure", {}).get("title"),
            "title": title or (view or {}).get("disclosure", {}).get("title"),
            "filed_at": filed_at,
            "level": a.level,
            "stage": a.stage,
            **stage_fields(a),
            "combo_upgraded": a.combo_upgraded,
            "status": a.status,
            "created_at": a.created_at,
            "affected_count": len(customers),
            "my_pending_count": sum(c in my_ids for c in pending_customers(view)) if view else 0,
            "graph_error": graph_error if a.level in ALERT_LEVELS else None,
        })
    return result


@router.get("/{assessment_id}")
async def get_alert(
    assessment_id: int,
    pb: CurrentPB = Depends(get_current_pb),
    db: Session = Depends(get_db),
    adapter: GraphAdapter = Depends(get_graph_adapter),
) -> dict:
    a = get_assessment(db, assessment_id)
    require_assessment_access(db, pb, a)
    require_legacy_runtime(pb)
    company = db.get(Company, a.corp_code)
    disclosure = db.get(Disclosure, a.rcept_no)
    view, graph_error = await read_state(adapter, a.rcept_no)
    affected = (view or {}).get("affected_customers", [])  # 보유 평가금액 큰 순 (find_customers가 정렬)
    people = _customers(db, [c["customer_id"] for c in affected])

    customers = []
    for c in affected:
        cust = people.get(c["customer_id"])
        mine = cust is not None and cust.pb_id == pb.pb_id and cust.tenant_id == pb.tenant_id
        customers.append({
            "customer_id": c["customer_id"],  # 옛 체크포인트의 priority·impact 필드가 새어 나가지 않게 필요한 것만 담는다
            "eval_amount": c.get("eval_amount"),
            "name": cust.name if mine else masked_name(c["customer_id"]),
            "pb_id": cust.pb_id if cust else None,
            "mine": mine,
            "risk_profile": cust.risk_profile if mine else None,
            "decision": decision_status(view, c["customer_id"]),
        })

    previous = db.scalars(
        select(RiskAssessment)
        .where(RiskAssessment.corp_code == a.corp_code, RiskAssessment.assessment_id < a.assessment_id,
               RiskAssessment.status.not_in(("RUNNING", "FAILED", "MANUAL_CHECK")))
        .order_by(RiskAssessment.assessment_id.desc())
    ).first()
    return {
        "assessment_id": a.assessment_id,
        "rcept_no": a.rcept_no,
        "status": a.status,
        "level": a.level,
        "stage": a.stage,
        **stage_fields(a),
        "combo_upgraded": a.combo_upgraded,
        "summary": impact_summary(a, previous, customers if view is not None else None),
        "reasons": strip_scores(a.reasons),  # 위험 항목·근거 (점수 숫자 없음, 점수 높은 순)
        "ai_explanation": public_explanation(a.ai_explanation),
        "created_at": a.created_at,
        "company": {"corp_code": a.corp_code, "name": company.name if company else None,
                    "stock_code": company.stock_code if company else (view or {}).get("disclosure", {}).get("stock_code"),
                    "market": company.market if company else None},
        "disclosure": {"rcept_no": a.rcept_no,
                       "title": disclosure.title if disclosure else (view or {}).get("disclosure", {}).get("title"),
                       "filed_at": disclosure.filed_at if disclosure else None,
                       "url": disclosure.url if disclosure else None},
        "evidence": (view or {}).get("evidence", []),
        "guardrail_flags": (view or {}).get("guardrail_flags", []),
        "customers": customers,
        "pending": pending_customers(view) if view else [],
        "graph_state_available": view is not None,
        "graph_error": graph_error,
    }


@router.get("/{assessment_id}/drafts")
async def get_drafts(
    assessment_id: int,
    pb: CurrentPB = Depends(get_current_pb),
    db: Session = Depends(get_db),
    adapter: GraphAdapter = Depends(get_graph_adapter),
) -> dict:
    """로그인한 PB의 담당 고객 안내문만 돌려준다."""
    a = get_assessment(db, assessment_id)
    require_assessment_access(db, pb, a)
    require_legacy_runtime(pb)
    view, graph_error = await read_state(adapter, a.rcept_no)
    if graph_error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, graph_error)
    if view is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Graph 상태가 없습니다. 공시를 다시 실행해야 합니다.")

    order = {c["customer_id"]: i for i, c in enumerate(view.get("affected_customers", []))}
    people = _customers(db, list(view.get("drafts", {})))
    drafts = []
    for cid, text in view.get("drafts", {}).items():
        cust = people.get(cid)
        if cust is None or cust.pb_id != pb.pb_id:
            continue
        decided = view.get("decided", {}).get(cid) or {}
        drafts.append({
            "customer_id": cid,
            "name": cust.name,
            "risk_profile": cust.risk_profile,
            "draft": text,  # {고객명} 자리표시 그대로 — 수정할 때 이 형태로 돌려보낸다
            "preview": fill_name(text, cust.name),  # 화면 표시용
            "decision": decision_status(view, cid),
            # 가드레일 재작성 3회 실패 → 초안이 비워지고 PB가 직접 써야 한다 (수정 후 승인만 가능)
            "manual": cid in view.get("manual_drafts", []),
            "edited_draft": decided.get("edited_draft"),
            "reject_reason": decided.get("reject_reason"),
        })
    drafts.sort(key=lambda d: order.get(d["customer_id"], 9999))
    return {
        "assessment_id": a.assessment_id,
        "rcept_no": a.rcept_no,
        "status": a.status,
        "guardrail_flags": view.get("guardrail_flags", []),
        "drafts": drafts,
    }
