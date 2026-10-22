"""POST /alerts/{assessment_id}/decision — PB 결정 → Graph 재개 (CLAUDE.md 8장, 9장 9~12단계).

- PB는 자기 담당 고객만 결정할 수 있다. 다른 PB 고객이 하나라도 섞이면 요청 전체를 403으로 거절한다.
- 반려는 사유가 필수다.
- 결정은 audit_logs에 남기고, 업무 생성은 Graph(create_tasks)가 한다. API는 contact_tasks를 직접 만들지 않는다.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.audit import write_audit
from api.auth import CurrentPB, get_current_pb
from api.deps import get_db
from api.tenancy import require_assessment_access, require_legacy_runtime
from api.graph_adapter import GraphAdapter, get_graph_adapter, now_iso, pending_customers
from api.routes.alerts import GRAPH_DOWN, NAME_PLACEHOLDER, get_assessment, read_state
from api.run_manager import RunConflict, RunManager, get_run_manager
from shared.models import Customer

router = APIRouter(prefix="/alerts", tags=["approvals"])


class CustomerDecision(BaseModel):
    customer_id: str
    action: Literal["approve", "reject", "rewrite"]
    edited_draft: str | None = None  # 수정 후 승인일 때만. {고객명} 자리표시 형태
    reject_reason: str | None = None

    @model_validator(mode="after")
    def check(self):
        if self.action == "reject" and not (self.reject_reason or "").strip():
            raise ValueError("반려는 사유가 필요합니다.")
        if self.action != "approve":
            self.edited_draft = None
        elif self.edited_draft is not None and not self.edited_draft.strip():
            raise ValueError("수정한 안내문이 비어 있습니다.")
        return self


class DecisionRequest(BaseModel):
    decisions: list[CustomerDecision] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self):
        ids = [d.customer_id for d in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("같은 고객이 두 번 들어 있습니다.")
        return self


@router.post("/{assessment_id}/decision", status_code=status.HTTP_202_ACCEPTED)
async def decide(
    assessment_id: int,
    body: DecisionRequest,
    pb: CurrentPB = Depends(get_current_pb),
    db: Session = Depends(get_db),
    adapter: GraphAdapter = Depends(get_graph_adapter),
    manager: RunManager = Depends(get_run_manager),
) -> dict:
    a = get_assessment(db, assessment_id)
    require_assessment_access(db, pb, a)
    require_legacy_runtime(pb)
    view, graph_error = await read_state(adapter, a.rcept_no)
    if graph_error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, graph_error)
    if view is None or view.get("status") != "WAITING_APPROVAL":
        raise HTTPException(status.HTTP_409_CONFLICT, "승인 대기 중인 경보가 아닙니다.")

    pending = set(pending_customers(view))
    ids = [d.customer_id for d in body.decisions]
    not_pending = [cid for cid in ids if cid not in pending]
    if not_pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"이미 결정했거나 승인 대상이 아닌 고객: {', '.join(not_pending)}")

    customers = {c.customer_id: c for c in db.scalars(select(Customer).where(Customer.customer_id.in_(ids)))}
    others = [cid for cid in ids if cid not in customers or customers[cid].pb_id != pb.pb_id or customers[cid].tenant_id != pb.tenant_id]
    if others:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"담당 고객이 아닙니다: {', '.join(others)}")

    decided_at = now_iso()
    decisions = []
    for d in body.decisions:
        item = d.model_dump()
        if d.edited_draft is not None:
            # PB가 실명을 직접 써도 저장·Graph에는 자리표시로 넘긴다 (LLM은 실명을 보지 않는다)
            item["edited_draft"] = d.edited_draft.strip().replace(customers[d.customer_id].name, NAME_PLACEHOLDER)
        decisions.append(item)

    # PBContext.allowed_customer_ids: 토큰의 PB가 DB에서 담당하는 고객 (요청 값을 그대로 쓰지 않는다)
    allowed = sorted(db.scalars(select(Customer.customer_id).where(Customer.pb_id == pb.pb_id, Customer.tenant_id == pb.tenant_id)))
    payload = {"pb_id": pb.pb_id, "decided_at": decided_at, "allowed_customer_ids": allowed, "decisions": decisions}

    # 진짜 Graph는 202 전에 같은 검사(권한, 상태, 수정안 가드레일)를 먼저 한다
    precheck = getattr(adapter, "precheck", None)
    if precheck is not None:
        try:
            await precheck(a.rcept_no, payload)
        except PermissionError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
        except Exception:  # 검사 중 Graph 연결이 끊김 — 결정은 기록하지 않는다
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GRAPH_DOWN)

    for item in decisions:
        action = "decision.approve_edited" if item["edited_draft"] else f"decision.{item['action']}"
        write_audit(db, pb.pb_id, action, f"assessment:{assessment_id}/customer:{item['customer_id']}",
                    {"rcept_no": a.rcept_no, **item, "decided_at": decided_at})

    try:
        manager.resume(a.rcept_no, payload)
    except RunConflict as exc:
        db.rollback()  # 재개를 못 했으면 결정 기록도 남기지 않는다
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))

    return {"assessment_id": assessment_id, "rcept_no": a.rcept_no, "accepted": len(decisions),
            "events_url": f"/runs/{a.rcept_no}/events"}
