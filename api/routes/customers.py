"""GET /customers — 내 담당 고객의 보유 종목과 위험 노출 (읽기 전용). (담당: R4)

PB가 자기 고객의 보유 종목과, 지금 경보가 걸린 종목에 대한 노출을 한 화면에서 본다.
- 로그인한 PB의 담당 고객(customers.pb_id)만 돌려준다. 다른 PB의 고객은 이름도 id도 나오지 않는다.
- 쓰기는 없다. 고객 연락(우선순위·안내문·연락 업무·이력)은 삭제됐다.
- 노출은 '가장 최근의 High·Critical 평가'를 보유 종목과 이어 보여 주기만 한다 (🔴/🟠/🟡 단계로 바뀔 예정).
"""
import json
from functools import lru_cache

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import CurrentPB, get_current_pb
from api.deps import get_db
from api.routes.alerts import ALERT_LEVELS
from shared.config import ROOT_DIR
from shared.models import Company, Customer, Holding, RiskAssessment

router = APIRouter(prefix="/customers", tags=["customers"])

LEVEL_RANK = {"Critical": 0, "High": 1}


@lru_cache
def _seed_stock_names() -> dict[str, str]:
    """DB에 회사 정보가 없는 종목(삼성전자 등)의 이름. data/seed/customers.json의 stocks 표를 쓴다."""
    try:
        data = json.loads((ROOT_DIR / "data" / "seed" / "customers.json").read_text(encoding="utf-8"))
        return {s["stock_code"]: s["name"] for s in data.get("stocks", [])}
    except (OSError, ValueError, KeyError):
        return {}


def _stock_names(db: Session) -> dict[str, str]:
    names = dict(_seed_stock_names())
    names.update({c.stock_code: c.name for c in db.scalars(select(Company))})  # DB의 회사명이 우선
    return names


def _recent_alerts(db: Session) -> dict[str, tuple[RiskAssessment, str]]:
    """종목코드 → (가장 최근의 High·Critical 평가, 회사명). 실패한 평가는 제외한다."""
    out: dict[str, tuple[RiskAssessment, str]] = {}
    rows = db.execute(
        select(RiskAssessment, Company)
        .join(Company, Company.corp_code == RiskAssessment.corp_code)
        .where(RiskAssessment.level.in_(ALERT_LEVELS), RiskAssessment.status != "FAILED")
        .order_by(RiskAssessment.assessment_id)
    ).all()
    for a, c in rows:  # 오름차순이라 뒤에 오는 것이 최근
        out[c.stock_code] = (a, c.name)
    return out


@router.get("")
def list_customers(pb: CurrentPB = Depends(get_current_pb), db: Session = Depends(get_db)) -> dict:
    customers = {c.customer_id: c for c in db.scalars(select(Customer).where(Customer.pb_id == pb.pb_id))}
    if not customers:
        return {"summary": {"customers": 0, "exposed": 0, "critical": 0, "high": 0}, "customers": []}

    names = _stock_names(db)
    alerts = _recent_alerts(db)
    holdings: dict[str, list[Holding]] = {cid: [] for cid in customers}
    for h in db.scalars(select(Holding).where(Holding.customer_id.in_(list(customers)))):
        holdings[h.customer_id].append(h)

    rows = []
    for cid, cust in customers.items():
        items, exposure = [], []
        for h in sorted(holdings[cid], key=lambda x: -x.eval_amount):
            alert = None
            if h.stock_code in alerts:
                a, corp = alerts[h.stock_code]
                alert = {"level": a.level, "assessment_id": a.assessment_id, "rcept_no": a.rcept_no,
                         "corp_name": corp, "created_at": a.created_at}
                exposure.append(alert)
            items.append({
                "stock_code": h.stock_code, "stock_name": names.get(h.stock_code, h.stock_code),
                "eval_amount": h.eval_amount,
                "ratio_pct": round(h.eval_amount / cust.total_assets * 100, 1) if cust.total_assets else None,
                "on_credit": h.on_credit, "alert": alert,
            })
        top = min((e["level"] for e in exposure), key=lambda lv: LEVEL_RANK.get(lv, 9), default=None)
        rows.append({
            "customer_id": cid, "name": cust.name, "risk_profile": cust.risk_profile, "total_assets": cust.total_assets,
            "holdings": items,
            "exposure": {
                "alert_count": len(exposure), "top_level": top,
                "amount": sum(i["eval_amount"] for i in items if i["alert"]),
            },
        })

    # 위험 노출이 큰 고객(Critical → High → 노출 없음)을 위로, 같으면 자산이 큰 순
    rows.sort(key=lambda r: (LEVEL_RANK.get(r["exposure"]["top_level"], 9), -r["total_assets"]))
    summary = {
        "customers": len(rows),
        "exposed": sum(1 for r in rows if r["exposure"]["alert_count"]),
        "critical": sum(1 for r in rows if r["exposure"]["top_level"] == "Critical"),
        "high": sum(1 for r in rows if r["exposure"]["top_level"] == "High"),
    }
    return {"summary": summary, "customers": rows}
