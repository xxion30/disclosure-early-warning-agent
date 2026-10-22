import asyncio
from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.runtime import AgentRuntime
from conftest import RCEPT


@pytest.mark.parametrize("level", ["Low", "Medium", "High", "Critical"])
def test_level_routes(kit, level):
    async def scenario():
        kit.tools.level = level
        runtime = AgentRuntime(kit.deps, InMemorySaver())
        state = await runtime.start(RCEPT)
        assert state["error"] is None, state
        assert state["total_score"] == 2.5
        assert state["risk_level"] == level
        assert not kit.store.tasks
        if level in ("High", "Critical"):
            assert state["status"] == "WAITING_APPROVAL"
            assert len(state["drafts"]) == 7
            assert len([p for p, _ in kit.model.calls if p == "draft_message"]) == 1
            assert len(state["affected_customers"]) == 7
        else:
            assert state["status"] == "DONE"
            assert "compute_customer_impact" not in [name for name, _ in kit.tools.calls]
        again = await runtime.start(RCEPT)
        assert again == state
        assert "body_text" not in state["disclosure"]
        assert dict(kit.tools.calls)["calculate_risk_score"]["as_of"] == "2025-04-01"
    asyncio.run(scenario())


def test_irrelevant_disclosure_stops(kit):
    async def scenario():
        runtime = AgentRuntime(replace(kit.deps, classify=lambda _: None), InMemorySaver())
        state = await runtime.start(RCEPT)
        assert state["status"] == "DONE"
        assert not kit.model.calls
    asyncio.run(scenario())


def test_no_eligible_customers(kit):
    async def scenario():
        kit.tools.customers = [{**kit.tools.customers[0], "priority": "P3"}]
        state = await AgentRuntime(kit.deps, InMemorySaver()).start(RCEPT)
        assert state["status"] == "DONE"
        assert state["drafts"] == {}
    asyncio.run(scenario())


def test_concurrent_start_does_not_repeat_analysis(kit):
    async def scenario():
        runtime = AgentRuntime(kit.deps, InMemorySaver())
        first, second = await asyncio.gather(runtime.start(RCEPT), runtime.start(RCEPT))
        assert first == second
        assert [n for n, _ in kit.tools.calls].count("get_disclosure") == 1
    asyncio.run(scenario())
