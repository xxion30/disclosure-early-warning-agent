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
        # 고객 연락(안내문·승인·업무)은 삭제됐다: 어떤 등급이든 분석·설명·저장 후 DONE으로 끝난다
        assert state["status"] == "DONE"
        assert state["ai_explanation"]
        assert not [p for p, _ in kit.model.calls if p in ("draft_message", "rewrite_message")]
        assert [(s, st) for s, st in kit.store.saved][-1][1] == "DONE"
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


def test_concurrent_start_does_not_repeat_analysis(kit):
    async def scenario():
        runtime = AgentRuntime(kit.deps, InMemorySaver())
        first, second = await asyncio.gather(runtime.start(RCEPT), runtime.start(RCEPT))
        assert first == second
        assert [n for n, _ in kit.tools.calls].count("get_disclosure") == 1
    asyncio.run(scenario())
