"""경보 목록·상세. (담당: R4)

- 경보 숫자(등급, 점수, 점수 내역, 상태)는 DB risk_assessments에서 읽는다.
- 근거 인용은 Graph State(체크포인트)에서 읽는다.
- Graph(MCP 서버·모델)에 붙지 못해도 목록·상세는 DB 내용으로 계속 보여준다 (graph_error로 알림).
- 고객 연락(안내문·PB 승인·연락 업무)은 삭제됐다. DARTCH의 종목별 위험 상태(/states)는 이 라우트를 대체한다.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from api.auth import CurrentPB, get_current_pb
from api.deps import get_db
from api.graph_adapter import GraphAdapter, get_graph_adapter
from shared.models import Company, Disclosure, RiskAssessment

router = APIRouter(prefix="/alerts", tags=["alerts"])

ALERT_LEVELS = ("High", "Critical")  # 경보로 보는 등급 (🔴/🟠/🟡 단계로 바뀔 예정)
GRAPH_DOWN = "진행 엔진(Graph)에 연결할 수 없습니다. MCP 서버(8001·8002)와 .env의 API 키를 확인하세요."

log = logging.getLogger("api.alerts")


async def read_state(adapter: GraphAdapter, rcept_no: str) -> tuple[dict | None, str | None]:
    """(State, 오류 문구). 진짜 모드에서 MCP 서버가 꺼져 Dependencies 조립이 실패해도 예외를 올리지 않는다."""
    try:
        return await adapter.get_state(rcept_no), None
    except Exception as exc:
        log.warning("Graph 상태 읽기 실패 %s: %s: %s", rcept_no, type(exc).__name__, exc)
        return None, GRAPH_DOWN


def get_assessment(db: Session, assessment_id: int) -> RiskAssessment:
    row = db.get(RiskAssessment, assessment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "경보를 찾을 수 없습니다.")
    return row


LEVEL_ORDER = ("Low", "Medium", "High", "Critical")


def impact_summary(a: RiskAssessment, previous: RiskAssessment | None) -> dict:
    """경보 상단용 '이번 공시의 영향도' 요약. AI 문장이 아니라 저장된 점수 내역에서 규칙으로 만든다.

    - this_filing: 이번 공시(rcept_no)가 점수 내역에 올린 항목과 점수
    - score_change / level_change: 같은 회사의 직전 평가와 비교
    """
    rows = [r for r in (a.reasons or []) if isinstance(r, dict)]
    mine = [r for r in rows if r.get("rcept_no") == a.rcept_no and not r.get("from_history") and r.get("group") in ("A", "B")]
    items = []
    for r in mine:
        quote = r.get("quote") or next((e.get("quote") for e in r.get("evidence") or [] if isinstance(e, dict)), None)
        items.append({
            "item": r.get("item") or r.get("signal_type"), "signal_type": r.get("signal_type"), "group": r.get("group"),
            "points": round(float(r.get("points") or 0), 2), "fixed_level": r.get("fixed_level"),
            "quote": (quote[:140] + "…") if quote and len(quote) > 140 else quote,
        })
    this_points = round(sum(i["points"] for i in items), 2)
    fixed = [i["fixed_level"] for i in items if i["fixed_level"]]
    fixed_level = max(fixed, key=LEVEL_ORDER.index) if fixed else None
    total = a.total_score
    prev_ok = previous is not None and previous.total_score is not None
    score_change = round(total - previous.total_score, 2) if prev_ok and total is not None else None

    if fixed_level:
        headline = f"확정 악재({'·'.join(i['item'] for i in items if i['fixed_level'])})로 등급이 {fixed_level}로 고정됐습니다."
    elif items and this_points > 0:
        headline = f"이번 공시로 숨은 신호 {len(items)}건(+{this_points}점)이 추가됐습니다."
    else:
        headline = "이번 공시로 추가된 위험 신호는 없습니다. 기존 신호 누적으로 등급이 유지됩니다."
    if prev_ok and previous.level != a.level:
        headline += f" 등급 {previous.level} → {a.level}."

    return {
        "headline": headline,
        "previous": {"assessment_id": previous.assessment_id, "level": previous.level, "total_score": previous.total_score} if prev_ok else None,
        "score_change": score_change,
        "this_filing": {"points": this_points, "items": items, "fixed_level": fixed_level},
        "carried_score": round(total - this_points, 2) if total is not None else None,
    }


@router.get("")
async def list_alerts(
    level: list[str] | None = Query(None, description="여러 개 가능: ?level=High&level=Critical"),
    status_: str | None = Query(None, alias="status"),
    date: str | None = Query(None, description="YYYY-MM-DD. 공시일 또는 생성일 (일일 요약 탭)"),
    pb: CurrentPB = Depends(get_current_pb),
    db: Session = Depends(get_db),
) -> list[dict]:
    q = (
        select(RiskAssessment, Company.name, Disclosure.title, Disclosure.filed_at)
        .outerjoin(Company, Company.corp_code == RiskAssessment.corp_code)
        .outerjoin(Disclosure, Disclosure.rcept_no == RiskAssessment.rcept_no)
        .order_by(RiskAssessment.created_at.desc(), RiskAssessment.assessment_id.desc())
    )
    if level:
        q = q.where(RiskAssessment.level.in_(level))
    if status_:
        q = q.where(RiskAssessment.status == status_)
    if date:
        q = q.where(or_(Disclosure.filed_at.startswith(date), RiskAssessment.created_at.startswith(date)))
    return [{
        "assessment_id": a.assessment_id,
        "rcept_no": a.rcept_no,
        "corp_name": corp_name,
        "title": title,
        "filed_at": filed_at,
        "level": a.level,
        "total_score": a.total_score,
        "combo_upgraded": a.combo_upgraded,
        "status": a.status,
        "created_at": a.created_at,
    } for a, corp_name, title, filed_at in db.execute(q).all()]


@router.get("/{assessment_id}")
async def get_alert(
    assessment_id: int,
    pb: CurrentPB = Depends(get_current_pb),
    db: Session = Depends(get_db),
    adapter: GraphAdapter = Depends(get_graph_adapter),
) -> dict:
    a = get_assessment(db, assessment_id)
    company = db.get(Company, a.corp_code)
    disclosure = db.get(Disclosure, a.rcept_no)
    view, graph_error = await read_state(adapter, a.rcept_no)
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
        "total_score": a.total_score,
        "combo_upgraded": a.combo_upgraded,
        "summary": impact_summary(a, previous),
        "reasons": a.reasons,  # 점수 내역
        "ai_explanation": a.ai_explanation,
        "created_at": a.created_at,
        "company": {"corp_code": a.corp_code, "name": company.name if company else None,
                    "stock_code": company.stock_code if company else (view or {}).get("disclosure", {}).get("stock_code"),
                    "market": company.market if company else None},
        "disclosure": {"rcept_no": a.rcept_no,
                       "title": disclosure.title if disclosure else (view or {}).get("disclosure", {}).get("title"),
                       "filed_at": disclosure.filed_at if disclosure else None,
                       "url": disclosure.url if disclosure else None},
        "evidence": (view or {}).get("evidence", []),
        "graph_state_available": view is not None,
        "graph_error": graph_error,
    }
