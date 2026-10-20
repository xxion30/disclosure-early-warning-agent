"""Serializable graph state and validated model/API payloads.

Additional fields are A-local; see INTEGRATION.md before binding other components.
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


class FactsOutput(StrictModel):
    facts: list[Fact] = Field(min_length=1)


class ExplanationItem(StrictModel):
    breakdown_index: int = Field(ge=0)
    explanation: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)


class ExplanationOutput(StrictModel):
    items: list[ExplanationItem] = Field(min_length=1)


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
    last_node: str
    failure_recording_error: str


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
