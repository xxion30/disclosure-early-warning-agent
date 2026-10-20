"""GET /customers — 내 담당 고객 현황 (읽기 전용).

PB가 자기 고객의 보유 종목, 지금 경보가 걸린 종목에 대한 노출, 연락 이력을 한 화면에서 보게 한다.
- 로그인한 PB의 담당 고객(customers.pb_id)만 돌려준다. 다른 PB의 고객은 이름도 id도 나오지 않는다.
- 쓰기는 없다. 연락 업무의 상태 변경은 /tasks, 안내문 승인은 /alerts/{id}/decision에서 한다.
- 경보 노출의 우선순위(P1·P2·P3)는 경보 화면과 같은 영향도 규칙(rules.yaml)으로 계산한다.
"""
import json
from functools import lru_cache

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import CurrentPB, get_current_pb
from api.deps import get_db
from api.routes.alerts import ALERT_LEVELS, PRIORITY_ORDER, fill_name
from mcp_servers.business.rules.impact_rules import ImpactInputError, customer_impact
from shared.config import ROOT_DIR
from shared.models import AuditLog, Company, ContactTask, Customer, Holding, RiskAssessment

router = APIRouter(prefix="/customers", tags=["customers"])


@lru_cache
def _seed_stock_names() -> dict[str, str]:
    """DB에 회사 정보가 없는 종목(삼성전자 등)의 이름. data/customers.json의 stocks 표를 쓴다."""
    try:
        data = json.loads((ROOT_DIR / "data" / "customers.json").read_text(encoding="utf-8"))
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


def _history(db: Session, customers: dict[str, Customer], corp_names: dict[int, tuple[str | None, str | None]]) -> dict[str, list[dict]]:
    """고객별 연락 이력: 연락 업무(승인·연락완료·보류)와 반려 결정을 시간 역순으로."""
    hist: dict[str, list[dict]] = {cid: [] for cid in customers}
    tasks = db.scalars(select(ContactTask).where(ContactTask.customer_id.in_(list(customers) or [""])))
    for t in tasks:
        name = customers[t.customer_id].name
        corp = corp_names.get(t.assessment_id, (None, None))[0]
        hist[t.customer_id].append({
            "kind": "task", "task_id": t.task_id, "assessment_id": t.assessment_id, "corp_name": corp,
            "priority": t.priority, "status": t.status, "note": t.result_note or t.reject_reason,
            "message_preview": fill_name(t.draft_message, name), "when": t.updated_at or t.created_at,
        })
    rejects = db.scalars(select(AuditLog).where(AuditLog.action == "decision.reject"))
    for log in rejects:
        # 대상 형식: "assessment:{id}/customer:{customer_id}" (api/routes/approvals.py가 기록)
        payload = log.payload or {}
        cid = payload.get("customer_id")
        if cid not in hist:
            continue
        aid = int(log.target.split(":")[1].split("/")[0]) if log.target and log.target.startswith("assessment:") else None
        hist[cid].append({
            "kind": "reject", "task_id": None, "assessment_id": aid, "corp_name": corp_names.get(aid, (None, None))[0],
            "priority": None, "status": "반려", "note": payload.get("reject_reason"), "message_preview": None,
            "when": payload.get("decided_at") or log.created_at,
        })
    for items in hist.values():
        items.sort(key=lambda x: x["when"] or "", reverse=True)
    return hist


@router.get("")
def list_customers(pb: CurrentPB = Depends(get_current_pb), db: Session = Depends(get_db)) -> dict:
    customers = {c.customer_id: c for c in db.scalars(select(Customer).where(Customer.pb_id == pb.pb_id))}
    if not customers:
        return {"summary": {"customers": 0, "exposed": 0, "p1": 0, "p2": 0, "tasks": {}}, "customers": []}

    names = _stock_names(db)
    alerts = _recent_alerts(db)
    holdings: dict[str, list[Holding]] = {cid: [] for cid in customers}
    for h in db.scalars(select(Holding).where(Holding.customer_id.in_(list(customers)))):
        holdings[h.customer_id].append(h)

    corp_names = {
        a.assessment_id: (c.name, c.stock_code)
        for a, c in db.execute(select(RiskAssessment, Company).join(Company, Company.corp_code == RiskAssessment.corp_code)).all()
    }
    history = _history(db, customers, corp_names)

    rows = []
    for cid, cust in customers.items():
        items, exposure = [], []
        for h in sorted(holdings[cid], key=lambda x: -x.eval_amount):
            alert = None
            if h.stock_code in alerts:
                a, corp = alerts[h.stock_code]
                try:
                    impact = customer_impact(
                        eval_amount=h.eval_amount, total_assets=cust.total_assets, risk_profile=cust.risk_profile,
                        on_credit=h.on_credit, company_level=a.level,
                    )
                except ImpactInputError:  # 알 수 없는 투자성향 등. 화면이 깨지지 않게 우선순위만 비운다
                    impact = {"priority": None, "impact_score": None, "impact_level": None}
                alert = {
                    "level": a.level, "assessment_id": a.assessment_id, "rcept_no": a.rcept_no, "corp_name": corp,
                    "priority": impact["priority"], "impact_score": impact["impact_score"], "impact_level": impact["impact_level"],
                    "created_at": a.created_at,
                }
                exposure.append(alert)
            items.append({
                "stock_code": h.stock_code, "stock_name": names.get(h.stock_code, h.stock_code),
                "eval_amount": h.eval_amount,
                "ratio_pct": round(h.eval_amount / cust.total_assets * 100, 1) if cust.total_assets else None,
                "on_credit": h.on_credit, "alert": alert,
            })
        top = min((e["priority"] for e in exposure if e["priority"]), key=lambda p: PRIORITY_ORDER[p], default=None)
        rows.append({
            "customer_id": cid, "name": cust.name, "risk_profile": cust.risk_profile, "total_assets": cust.total_assets,
            "holdings": items,
            "exposure": {
                "alert_count": len(exposure), "top_priority": top,
                "amount": sum(i["eval_amount"] for i in items if i["alert"]),
            },
            "history": history[cid],
            "last_contact": next((x for x in history[cid]), None),
        })

    # 연락이 급한 고객(P1 → P2 → P3 → 노출 없음)을 위로, 같으면 자산이 큰 순
    rows.sort(key=lambda r: (PRIORITY_ORDER.get(r["exposure"]["top_priority"], 9), -r["total_assets"]))

    task_counts: dict[str, int] = {}
    for r in rows:
        for x in r["history"]:
            task_counts[x["status"]] = task_counts.get(x["status"], 0) + 1
    summary = {
        "customers": len(rows),
        "exposed": sum(1 for r in rows if r["exposure"]["alert_count"]),
        "p1": sum(1 for r in rows if r["exposure"]["top_priority"] == "P1"),
        "p2": sum(1 for r in rows if r["exposure"]["top_priority"] == "P2"),
        "tasks": task_counts,
    }
    return {"summary": summary, "customers": rows}
