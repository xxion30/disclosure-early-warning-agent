"""체크포인트: 재시작 뒤에도 같은 thread_id로 이어서 실행되는가. (담당: R2)"""
import asyncio

from agent.runtime import open_runtime
from conftest import RCEPT


def test_restart_keeps_the_finished_state_and_does_not_rerun(kit, tmp_path):
    async def scenario():
        path = tmp_path / "checkpoints.db"
        async with open_runtime(kit.deps, path) as runtime:
            first = await runtime.start(RCEPT)
        assert first["status"] == "DONE" and first["error"] is None, first
        calls_before = len(kit.tools.calls)
        async with open_runtime(kit.deps, path) as runtime:
            assert (await runtime.get_state(RCEPT))["status"] == "DONE"
            again = await runtime.start(RCEPT)       # 끝난 실행은 암묵적으로 다시 시작하지 않는다
            assert again == first and again["trace_id"] == first["trace_id"]
        assert len(kit.tools.calls) == calls_before  # 도구를 다시 부르지 않았다
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
            assert state["status"] == "DONE" and state["trace_id"] == before["trace_id"]
            again = await runtime.start(RCEPT)            # 끝난 실행은 다시 시작하지 않는다
            assert again == state
    asyncio.run(scenario())
