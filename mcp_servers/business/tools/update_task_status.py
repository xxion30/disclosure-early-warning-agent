from mcp_servers.business.rules.loader import load_rules
from mcp_servers.business.tools._common import guarded, invalid, not_found, now_iso, task_dict
from shared.db import get_session
from shared.models import TASK_STATUSES, ContactTask
from shared.schemas import ok


@guarded
def update_task_status(task_id: int, status: str, note: str | None = None) -> dict:
    """연락 업무의 상태를 바꾼다. 모델은 호출하지 않는다 — PB가 화면에서 누를 때만 쓴다.

    승인·보류 상태에서 연락완료·보류로만 바꿀 수 있다 (rules.yaml의 task_transitions).

    Args:
        task_id: 연락 업무 id
        status: 연락완료 | 보류
        note: 결과 메모
    """
    if status not in TASK_STATUSES:
        raise invalid(f"status는 {', '.join(TASK_STATUSES)} 중 하나여야 합니다: {status!r}")
    transitions = load_rules()["task_transitions"]
    with get_session() as s:
        t = s.get(ContactTask, task_id)
        if not t:
            raise not_found(f"task_id {task_id} 업무를 찾을 수 없습니다.")
        if status not in transitions.get(t.status, []):
            raise invalid(f"'{t.status}' 상태에서 '{status}'(으)로 바꿀 수 없습니다.")
        t.status = status
        if note is not None:
            t.result_note = note
        t.updated_at = now_iso()
        s.flush()
        return ok(task_dict(t))
