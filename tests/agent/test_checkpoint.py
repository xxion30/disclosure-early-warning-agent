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


def test_start_continues_a_run_interrupted_by_a_crash(kit, tmp_path):
    """노드 실행 중 프로세스가 죽은 뒤 같은 공시가 다시 오면, 끝난 것으로 돌려주지 않고 이어서 실행한다."""
    async def scenario():
        path = tmp_path / "checkpoints.db"
        original = kit.tools.call

        async def crashing_call(name, arguments):
            if name == "calculate_risk_score":
                raise asyncio.CancelledError  # 프로세스 종료처럼 실행을 중단시킨다 (예외 처리 없이 전파)
            return await original(name, arguments)

        kit.tools.call = crashing_call
        async with open_runtime(kit.deps, path) as runtime:
            try:
                await runtime.start(RCEPT)
            except BaseException:  # noqa: BLE001 — LangGraph가 NodeCancelledError로 감싸 던진다
                pass
        kit.tools.call = original
        async with open_runtime(kit.deps, path) as runtime:
            before = await runtime.get_state(RCEPT)
            assert before["status"] == "RUNNING"          # 중간에 멈춘 체크포인트
            state = await runtime.start(RCEPT)            # 큐의 재전달
            assert state["status"] == "WAITING_APPROVAL" and state["trace_id"] == before["trace_id"]
            again = await runtime.start(RCEPT)            # 승인 대기 실행은 다시 시작하지 않는다
            assert again == state
    asyncio.run(scenario())
