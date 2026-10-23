"""agent.Dependencies 조립 (담당: R4): MCP 도구 + 모델 + Harness + 저장 도구를 한곳에서 묶는다.

API(`api/graph_adapter.default_dependencies`)는 여기서 만든 deps를 받기만 한다.
조각별 담당: 모델·인용 보정 agent/model_adapter.py(R2), 모델 Harness agent/harness_llm.py(R2),
도구 Harness·저장·감사 agent/adapters/(R4), 분류·신호 입력 agent/rules.py(R2).

    deps = await build_dependencies()          # business-mcp(8001)·rag-mcp(8002)가 떠 있어야 한다
    deps = await build_dependencies(model=...)  # 테스트: 모델(또는 tools)만 바꿔 끼운다
"""
from __future__ import annotations

from agent.adapters.harness_tools import default_harness
from agent.adapters.store import AssessmentStore, ToolTaskCreator, audit, authorize_task
from agent.model_adapter import UnsafeDraftInjector, build_model
from agent.adapters.phase1 import Phase1Model, EvidenceTools, make_phase1_classifier, signal_input
from agent.ports import Dependencies
from harness.tracing import enable_langsmith
from shared.rule_catalog import load_catalog
from shared.config import get_settings


async def build_dependencies(settings=None, *, tools=None, model=None, harness=None) -> Dependencies:
    """tools·model·harness를 비우면 운영 구성(MCP 서버 2개, Anthropic/OpenAI 모델, D의 Harness)을 만든다."""
    settings = settings or get_settings()
    enable_langsmith(settings)
    harness = harness or default_harness()
    if tools is None:
        from agent.adapters.mcp_client import MCPTools

        tools = await MCPTools.connect(settings)
    if model is None:
        model = build_model(harness, settings)
    if settings.FAKE_UNSAFE_DRAFT:
        model = UnsafeDraftInjector(model)
    return Dependencies(
        tools=EvidenceTools(tools, harness),
        model=Phase1Model(model, harness),
        harness=harness,
        persistence=AssessmentStore(tools, audit, harness, upsert_confirmed=True),
        classify=make_phase1_classifier(),
        signal_input=signal_input,
        create_task=ToolTaskCreator(tools, harness, authorize_task, idempotency_confirmed=True),
        quiet_types=frozenset(load_catalog()["quiet_types"]),
    )
