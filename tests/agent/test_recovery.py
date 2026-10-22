import asyncio
from dataclasses import replace

from langgraph.checkpoint.memory import InMemorySaver

from agent.ports import AgentError
from agent.runtime import AgentRuntime
from shared.schemas import fail
from conftest import RCEPT


def test_mcp_timeout_retries_once(kit):
    async def scenario():
        kit.tools.error = fail("TIMEOUT", "synthetic timeout")
        state = await AgentRuntime(kit.deps, InMemorySaver()).start(RCEPT)
        assert state["status"] == "MANUAL_CHECK"
        assert len(kit.tools.calls) == 2
        assert state["tool_call_counts"]["get_disclosure"] == 2
    asyncio.run(scenario())


def test_other_tool_error_does_not_retry_or_leak_message(kit):
    async def scenario():
        kit.tools.error = fail("NOT_FOUND", "sensitive untrusted error message")
        state = await AgentRuntime(kit.deps, InMemorySaver()).start(RCEPT)
        assert state["status"] == "MANUAL_CHECK"
        assert len(kit.tools.calls) == 1
        assert "sensitive" not in state["error"]
    asyncio.run(scenario())


def test_llm_failure_is_failed_and_audited(kit):
    async def scenario():
        kit.model.failure = AgentError("LLM 복구 실패", "FAILED")
        state = await AgentRuntime(kit.deps, InMemorySaver()).start(RCEPT)
        assert state["status"] == "FAILED"
        assert any(action == "agent_failure" for action, _, _ in kit.store.audits.values())
    asyncio.run(scenario())


def test_ai_turn_budget_ends_in_manual_check(kit):
    async def scenario():
        state = await AgentRuntime(replace(kit.deps, max_model_turns=1), InMemorySaver()).start(RCEPT)
        assert state["status"] == "MANUAL_CHECK"
    asyncio.run(scenario())


def test_execution_timeout(kit):
    async def scenario():
        original = kit.tools.call
        async def slow(name, arguments):
            await asyncio.sleep(0.05)
            return await original(name, arguments)
        kit.tools.call = slow
        state = await AgentRuntime(replace(kit.deps, execution_seconds=0.01), InMemorySaver()).start(RCEPT)
        assert state["status"] == "MANUAL_CHECK"
    asyncio.run(scenario())


def test_failure_recording_failure_is_visible(kit):
    async def scenario():
        kit.tools.error = fail("DB_ERROR", "test")
        async def broken(*args):
            raise RuntimeError("synthetic save outage")
        kit.store.save = broken
        state = await AgentRuntime(kit.deps, InMemorySaver()).start(RCEPT)
        assert state["status"] == "MANUAL_CHECK"
        assert "save:RuntimeError" in state["failure_recording_error"]
    asyncio.run(scenario())
