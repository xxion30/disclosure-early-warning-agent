from sqlalchemy import select

from risk_engine import impact_rules
from risk_engine.loader import load_rules
from mcp_servers.business.tools._common import guarded, invalid, not_found, now_iso, task_dict
from shared.db import get_session
from shared.models import Company, ContactTask, Customer, Holding, RiskAssessment
from shared.schemas import ok

MAX_DRAFT_CHARS = 4000


@guarded
def create_contact_task(assessment_id: int, customer_id: str, priority: str, draft_message: str) -> dict:
    """PB가 승인한 고객의 연락 업무를 만든다. PB 승인 뒤 Agent 코드(create_tasks 노드)만 호출한다.

    같은 (assessment_id, customer_id)로 다시 부르면 새로 만들지 않고 기존 업무를 돌려준다.
    우선순위는 규칙표로 다시 계산해 맞는지 확인한다(P1·P2만 허용).

    Args:
        assessment_id: 위험도 평가 id (상태가 WAITING_APPROVAL 또는 DONE이어야 함)
        customer_id: 고객 id (예: C001)
        priority: P1 또는 P2
        draft_message: PB가 승인한(또는 고친) 안내문. {고객명} 자리표시 그대로.
    """
    rules = load_rules()
    if priority not in rules["impact"]["contact_priorities"]:
        raise invalid(f"연락 업무는 {', '.join(rules['impact']['contact_priorities'])}만 만들 수 있습니다: {priority!r}")
    if not isinstance(draft_message, str) or not draft_message.strip():
        raise invalid("draft_message가 비어 있습니다.")
    if len(draft_message) > MAX_DRAFT_CHARS:
        raise invalid(f"draft_message는 {MAX_DRAFT_CHARS}자 이하여야 합니다.")
    with get_session() as s:
        a = s.get(RiskAssessment, assessment_id)
        if not a:
            raise not_found(f"assessment_id {assessment_id}를 찾을 수 없습니다.")
        if a.status not in ("WAITING_APPROVAL", "DONE"):
            raise invalid(f"승인 대기 중인 평가에서만 업무를 만들 수 있습니다 (현재 {a.status}).")
        customer = s.get(Customer, customer_id)
        if not customer:
            raise not_found(f"customer_id {customer_id} 고객을 찾을 수 없습니다.")
        company = s.get(Company, a.corp_code)
        holding = s.get(Holding, (customer_id, company.stock_code))
        if not holding:
            raise invalid(f"{customer_id}는 {company.stock_code}를 보유하지 않습니다.")

        impact = impact_rules.customer_impact(
            eval_amount=holding.eval_amount,
            total_assets=customer.total_assets,
            risk_profile=customer.risk_profile,
            on_credit=holding.on_credit,
            company_level=a.level,
            rules=rules,
        )
        if impact["priority"] != priority:
            raise invalid(f"{customer_id}의 우선순위는 규칙표로 {impact['priority']}입니다 (요청 {priority}).")

        existing = s.scalars(
            select(ContactTask).where(ContactTask.assessment_id == assessment_id, ContactTask.customer_id == customer_id)
        ).first()
        if existing:
            return ok({"task_id": existing.task_id, "created": False, "status": existing.status})

        now = now_iso()
        task = ContactTask(
            assessment_id=assessment_id,
            customer_id=customer_id,
            pb_id=customer.pb_id,
            priority=priority,
            impact_score=impact["impact_score"],
            draft_message=draft_message,
            status="승인",
            created_at=now,
            updated_at=now,
        )
        s.add(task)
        s.flush()
        return ok({"task_id": task.task_id, "created": True, "status": task.status})
