import asyncio

from agent.runtime import open_runtime
from conftest import RCEPT
from test_approval import P01, P02, approve


def test_restart_retains_pending_approvals_and_trace(kit, tmp_path):
    async def scenario():
        path = tmp_path / "checkpoints.db"
        async with open_runtime(kit.deps, path) as runtime:
            first = await runtime.start(RCEPT)
            trace_id = first["trace_id"]
        async with open_runtime(kit.deps, path) as runtime:
            assert (await runtime.get_state(RCEPT))["status"] == "WAITING_APPROVAL"
            state = await runtime.resume(RCEPT, approve(sorted(P01.allowed_customer_ids)), P01)
            assert len(state["task_ids"]) == 4
        async with open_runtime(kit.deps, path) as runtime:
            state = await runtime.resume(RCEPT, approve(sorted(P02.allowed_customer_ids)), P02)
            assert state["status"] == "DONE", state
            assert state["trace_id"] == trace_id
            assert state["trace_headers"] == first["trace_headers"]
            assert len(state["task_ids"]) == 7
            assert len(kit.store.tasks) == 7
    asyncio.run(scenario())


def test_partial_creation_failure_preserves_completed_ids(kit):
    async def scenario():
        from langgraph.checkpoint.memory import InMemorySaver
        from agent.runtime import AgentRuntime
        runtime = AgentRuntime(kit.deps, InMemorySaver())
        await runtime.start(RCEPT)
        kit.store.fail_customer = "C003"
        state = await runtime.resume(RCEPT, approve(["C001", "C003"]), P01)
        assert state["status"] == "MANUAL_CHECK"
        assert state["customer_tasks"] == {"C001": 1}
        assert len(kit.store.tasks) == 1
        # Replaying an already accepted decision must not silently restart failed work.
        again = await runtime.resume(RCEPT, approve(["C001", "C003"]), P01)
        assert again == state
        assert len(kit.store.create_attempts) == 2
    asyncio.run(scenario())


def test_retry_recovers_lost_write_reply_without_duplicate(kit):
    async def scenario():
        from langgraph.checkpoint.memory import InMemorySaver
        from agent.runtime import AgentRuntime
        runtime = AgentRuntime(kit.deps, InMemorySaver())
        await runtime.start(RCEPT)
        kit.store.fail_after_write = True
        state = await runtime.resume(RCEPT, approve(["C001", "C003"]), P01)
        assert state["status"] == "MANUAL_CHECK"
        assert len(kit.store.tasks) == 1
        assert state["customer_tasks"] == {}
        import pytest
        with pytest.raises(PermissionError):
            await runtime.retry_tasks(RCEPT, P02)
        state = await runtime.retry_tasks(RCEPT, P01)
        assert state["error"] is None, state
        assert state["status"] == "WAITING_APPROVAL"
        assert len(kit.store.tasks) == 2
        assert len(state["customer_tasks"]) == 2
    asyncio.run(scenario())
