import asyncio

from agent.execution import Execution, current_execution


async def handle_failure(state, deps):
    scope = Execution(seconds=15, max_model_turns=0)
    token = current_execution.set(scope)
    try:
        return await record_failure(state, deps, scope)
    finally:
        current_execution.reset(token)


async def record_failure(state, deps, scope):
    update = {}
    errors = []
    try:
        await asyncio.wait_for(deps.persistence.audit(state, "agent_failure", {
            "node": state.get("last_node"), "status": state["status"],
            "error": state["error"],
        }), timeout=5)
    except Exception as exc:
        errors.append(f"audit:{type(exc).__name__}")
    try:
        update["assessment_id"] = await asyncio.wait_for(
            deps.persistence.save(state, state["status"]), timeout=5)
    except Exception as exc:
        errors.append(f"save:{type(exc).__name__}")
    try:
        await asyncio.wait_for(deps.harness.event("failed", {
            "trace_id": state["trace_id"], "status": state["status"], "error": state["error"],
        }), timeout=5)
    except Exception as exc:
        errors.append(f"event:{type(exc).__name__}")
    if errors:
        update["failure_recording_error"] = ", ".join(errors)
    counts = dict(state.get("tool_call_counts", {}))
    for name, count in scope.tool_counts.items():
        counts[name] = counts.get(name, 0) + count
    update["tool_call_counts"] = counts
    return update
