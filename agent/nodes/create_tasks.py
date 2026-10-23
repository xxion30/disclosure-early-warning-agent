from agent.nodes.guardrail_check import message_flags
from agent.ports import AgentError
from agent.execution import current_execution
import hashlib
import json


async def create_tasks(state, deps):
    if deps.unified is not None:
        from agent.integration import ensure_current
        await ensure_current(state, deps)
    created = dict(state["customer_tasks"])
    pending = [(cid, decision) for cid, decision in state["decisions"].items()
               if decision["status"] == "approved" and cid not in created]
    if not pending:
        return {}
    cid, decision = pending[0]
    message = state["drafts"][cid]
    if await message_flags(message, deps):
        raise AgentError("업무 생성 전 최종 문안 검사 실패")
    # Each step checkpoints one task. Storage must still guarantee idempotency
    # across a crash between this write and the next graph checkpoint.
    scope = current_execution.get()
    before = scope.tool_counts.get("create_contact_task", 0)
    context = state.get('execution_context', {})
    task_id = await deps.create_task({
        "assessment_id": state["assessment_id"], "customer_id": cid,
        "draft_message": message,
        "pb_id": decision["pb_id"], "trace_id": state["trace_id"],
        "idempotency_key": (hashlib.sha256(json.dumps(
            [context['tenant_id'], context['assessment_key'], cid], ensure_ascii=False).encode()).hexdigest()
                            if deps.unified is not None else f'{state["assessment_id"]}:{cid}'),
        **({'tenant_id': context['tenant_id'], 'assessment_key': context['assessment_key'],
            'expected_state_version': state['score_result']['expected_state_version']}
           if deps.unified is not None else {}),
    })
    if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id <= 0:
        raise AgentError("업무 ID 형식 불일치")
    created[cid] = task_id
    if scope.tool_counts.get("create_contact_task", 0) == before:
        # Non-MCP bindings still report one successful mutation.
        scope.tool_counts["create_contact_task"] = before + 1
    return {"customer_tasks": created, "task_ids": list(created.values())}
