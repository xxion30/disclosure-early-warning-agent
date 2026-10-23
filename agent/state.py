"""Serializable graph state and validated model/API payloads.

Additional fields are A-local; see docs/contracts/agent.md before binding other components.
"""
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class APIEvidence(StrictModel):
    kind: Literal['api']
    api: str = Field(min_length=1)
    field: str = Field(min_length=1)
    rcept_no: str = Field(min_length=1)
    row_key: str | None
    value: Any
    unit: str | None
    period: str | None
    basis: str | None
    data_version: str | None


class FactsOutput(StrictModel):
    # 비어 있을 수 있다. 신호가 없는 것이 정상인 유형(Dependencies.quiet_types)만 조용히 끝나고, 그 밖의 유형은 재시도·수동 확인이다.
    facts: list[Fact]


class ExplanationItem(StrictModel):
    breakdown_index: int = Field(ge=0)
    explanation: str = Field(min_length=1)
    evidence: list[Evidence | APIEvidence] = Field(min_length=1)


class ExplanationOutput(StrictModel):
    items: list[ExplanationItem] = Field(min_length=1)


class ToolRequest(StrictModel):
    tool: Literal["search_disclosure_text", "search_reference"]
    query: str = Field(min_length=1, max_length=200)
    category: str | None = None  # search_reference일 때만: 상장규정 | 공시해설 | 회계용어 | 안내문가이드


class ToolSelection(StrictModel):
    # 비어 있으면 추가 조회가 필요 없다는 뜻이다.
    requests: list[ToolRequest] = Field(default_factory=list, max_length=2)


class DraftOutput(StrictModel):
    message: str = Field(min_length=1)


class CustomerDecision(StrictModel):
    customer_id: str = Field(min_length=1)
    status: Literal["approved", "rejected", "rewrite"]
    edited_message: str | None = None
    reject_reason: str | None = None

    @model_validator(mode="after")
    def validate_content(self):
        if self.status == "rejected" and not (self.reject_reason or "").strip():
            raise ValueError("반려 사유가 필요합니다")
        if self.edited_message is not None and not self.edited_message.strip():
            raise ValueError("수정 문안은 비어 있을 수 없습니다")
        return self


class ApprovalBatch(StrictModel):
    decisions: list[CustomerDecision] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_customers(self):
        ids = [item.customer_id for item in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("같은 고객의 중복 결정")
        return self


class PBContext(StrictModel):
    """Constructed by authenticated API code, never from a public request body."""
    pb_id: str = Field(min_length=1)
    allowed_customer_ids: frozenset[str]
    tenant_id: str | None = None


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
    affected_customers: list[dict[str, Any]]
    drafts: dict[str, str]
    guardrail_flags: list[str]
    approval: dict[str, Any]
    task_ids: list[int]
    error: str | None
    error_retryable: bool
    # A-local execution fields, requiring no DB schema modification.
    assessment_id: int
    status: str
    score_result: dict[str, Any]
    trace_id: str
    trace_headers: dict[str, str]
    fact_attempts: int
    draft_attempts: dict[str, int]
    manual_drafts: list[str]
    decisions: dict[str, dict[str, Any]]
    customer_tasks: dict[str, int]
    rewrite_customers: list[str]
    relevant: bool
    no_signal: bool
    last_node: str
    failure_recording_error: str
    # Phase 1: internal rule outputs; not a public API response.
    phase1: bool
    risk_routes: list[dict[str, Any]]
    flags: list[dict[str, Any]]
    red_result: dict[str, Any]
    orange_result: dict[str, Any]
    yellow_result: dict[str, Any]
    financial_snapshot: dict[str, Any]
    stage: str
    orange_score: float
    yellow_score: float
    combined_ratio: float
    flag_attempts: int
    explanation_attempts: int
    explanation_errors: list[str]
    execution_context: dict[str, Any]
    execution_budget: dict[str, Any]
    structured_facts: dict[str, Any]
    verified_sources: list[dict[str, Any]]
    input_manifest: dict[str, Any]
    analysis_complete: bool


def initial_state(rcept_no: str, trace_id: str) -> AgentState:
    return AgentState(
        disclosure={"rcept_no": rcept_no}, status="RUNNING", trace_id=trace_id,
        retry_count=0, tool_call_counts={}, fact_attempts=0, draft_attempts={},
        signals=[], evidence=[], affected_customers=[], drafts={}, guardrail_flags=[],
        approval={}, decisions={}, customer_tasks={}, task_ids=[], manual_drafts=[],
        rewrite_customers=[], error=None,
    )


class DraftsOutput(StrictModel):
    drafts: dict[str, str]
