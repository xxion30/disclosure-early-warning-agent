"""Serializable graph state and validated model payloads. (담당: R2)

Additional fields are A-local; see docs/contracts/agent.md before binding other components.
"""
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(StrictModel):
    source: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    rcept_no: str = Field(min_length=1)
    location: str = Field(min_length=1)


class Fact(StrictModel):
    signal_type: str = Field(min_length=1)
    values: dict[str, Any]
    evidence: list[Evidence] = Field(min_length=1)


class FactsOutput(StrictModel):
    # 비어 있을 수 있다. 신호가 없는 것이 정상인 유형(Dependencies.quiet_types)만 조용히 끝나고, 그 밖의 유형은 재시도·수동 확인이다.
    facts: list[Fact]


class ExplanationItem(StrictModel):
    breakdown_index: int = Field(ge=0)
    explanation: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)


class ExplanationOutput(StrictModel):
    items: list[ExplanationItem] = Field(min_length=1)


class ToolRequest(StrictModel):
    tool: Literal["search_disclosure_text", "search_reference"]
    query: str = Field(min_length=1, max_length=200)
    category: str | None = None  # search_reference일 때만: 상장규정 | 공시해설 | 회계용어 | 안내문가이드(Phase 1에서 위험 항목 해설로 바뀐다)


class ToolSelection(StrictModel):
    # 비어 있으면 추가 조회가 필요 없다는 뜻이다.
    requests: list[ToolRequest] = Field(default_factory=list, max_length=2)


class AgentState(TypedDict, total=False):
    disclosure: dict[str, Any]
    disclosure_type: str
    extracted_facts: dict[str, Any]
    evidence: list[dict[str, Any]]
    retry_count: int
    tool_call_counts: dict[str, int]
    signals: list[dict[str, Any]]
    total_score: float
    risk_level: str
    combo_upgraded: bool
    ai_explanation: str
    error: str | None
    # A-local execution fields, requiring no DB schema modification.
    assessment_id: int
    status: str
    score_result: dict[str, Any]
    trace_id: str
    trace_headers: dict[str, str]
    fact_attempts: int
    relevant: bool
    no_signal: bool
    last_node: str
    failure_recording_error: str


def initial_state(rcept_no: str, trace_id: str) -> AgentState:
    return AgentState(
        disclosure={"rcept_no": rcept_no}, status="RUNNING", trace_id=trace_id,
        retry_count=0, tool_call_counts={}, fact_attempts=0,
        signals=[], evidence=[], error=None,
    )
