"""GET /tasks — 내 연락 업무, PATCH /tasks/{task_id} — 연락완료·보류 처리 (CLAUDE.md 8장 /tasks).

TODO(B와 확인): 계약상 상태 변경은 business-mcp의 update_task_status 도구다.
지금은 같은 규칙으로 DB를 직접 고친다. B가 도구를 올리면 여기서 도구를 부르도록 바꿀 수 있다.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.audit import write_audit
from api.auth import CurrentPB, get_current_pb
from api.deps import get_db
from api.graph_adapter import now_iso
from api.routes.alerts import fill_name
from shared.models import Company, ContactTask, Customer, RiskAssessment

router = APIRouter(prefix="/tasks", tags=["tasks"])

# PB가 화면에서 바꿀 수 있는 상태와, 바꿀 수 있는 출발 상태
PB_SETTABLE = ("연락완료", "보류")
CHANGEABLE_FROM = ("승인", "보류")


def _task_dict(t: ContactTask, customer: Customer, corp_name: str | None, a: RiskAssessment | None) -> dict:
    return {
        "task_id": t.task_id,
        "assessment_id": t.assessment_id,
        "rcept_no": a.rcept_no if a else None,
        "corp_name": corp_name,
        "level": a.level if a else None,
        "customer_id": t.customer_id,
        "customer_name": customer.name,
        "draft_message": t.draft_message,
        "message_preview": fill_name(t.draft_message, customer.name),
        "status": t.status,
        "reject_reason": t.reject_reason,
        "result_note": t.result_note,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


def _query():
    return (
        select(ContactTask, Customer, Company.name, RiskAssessment)
        .join(Customer, Customer.customer_id == ContactTask.customer_id)
        .outerjoin(RiskAssessment, RiskAssessment.assessment_id == ContactTask.assessment_id)
        .outerjoin(Company, Company.corp_code == RiskAssessment.corp_code)
    )


@router.get("")
def list_tasks(
    status_: str | None = Query(None, alias="status"),
    pb: CurrentPB = Depends(get_current_pb),
    db: Session = Depends(get_db),
) -> list[dict]:
    q = _query().where(ContactTask.pb_id == pb.pb_id)
    if status_:
        q = q.where(ContactTask.status == status_)
    q = q.order_by(ContactTask.created_at.desc(), ContactTask.task_id.desc())
    return [_task_dict(*row) for row in db.execute(q).all()]


class TaskUpdate(BaseModel):
    status: Literal["연락완료", "보류"]
    note: str | None = None


@router.patch("/{task_id}")
def update_task(
    task_id: int,
    body: TaskUpdate,
    pb: CurrentPB = Depends(get_current_pb),
    db: Session = Depends(get_db),
) -> dict:
    row = db.execute(_query().where(ContactTask.task_id == task_id)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "업무를 찾을 수 없습니다.")
    task = row[0]
    if task.pb_id != pb.pb_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "담당 업무가 아닙니다.")
    if task.status not in CHANGEABLE_FROM:
        raise HTTPException(status.HTTP_409_CONFLICT, f"'{task.status}' 상태에서는 바꿀 수 없습니다.")

    before = task.status
    task.status = body.status
    if body.note is not None:
        task.result_note = body.note.strip() or None
    task.updated_at = now_iso()
    write_audit(db, pb.pb_id, "task.status", f"task:{task_id}",
                {"from": before, "to": body.status, "note": task.result_note})
    db.flush()
    return _task_dict(*row)
