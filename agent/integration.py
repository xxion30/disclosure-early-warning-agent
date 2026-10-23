"""R2 ports for R4's unified calculator and trusted dispatcher context.

No DB, collector or scoring imports. Assembly explicitly opts into this path.
"""
from dataclasses import dataclass
from hashlib import sha256
from typing import Awaitable, Callable, Literal
import json

from pydantic import Field, StrictBool, model_validator

from agent.phase1 import Stage
from agent.state import StrictModel


class ExecutionContext(StrictModel):
    """Construct ONLY in authenticated R4/dispatcher code, never request bodies."""
    assessment_key: str = Field(min_length=1)
    as_of: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    kind: Literal['public', 'review'] = 'public'
    tenant_id: str | None = None
    rules_version: str = Field(min_length=1)
    input_version: str = Field(min_length=1)
    facts_version: str | None = None
    run_id: str = Field(min_length=1)
    attempt: int = Field(default=1, ge=1, le=3, strict=True)
    model_requests_used: int = Field(default=0, ge=0, strict=True)
    active_seconds_used: float = Field(default=0, ge=0, allow_inf_nan=False)

    @model_validator(mode='after')
    def valid_context(self):
        from datetime import date
        date.fromisoformat(self.as_of)
        if self.kind == 'review' and not (self.tenant_id or '').strip():
            raise ValueError('조직별 검토에는 tenant_id가 필요합니다')
        return self

    def thread_id(self, receipt):
        # Length-prefixed JSON avoids ambiguous delimiters and hides identifiers.
        identity = [self.kind, self.tenant_id if self.kind == 'review' else None,
                    self.assessment_key, self.rules_version, self.input_version, self.as_of, receipt]
        digest = sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        return f'dartch:{self.kind}:{digest}'


class UnifiedResult(StrictModel):
    """R4 stages.calculate output. Preserve trusted metadata and legacy storage fields."""
    stage: Stage
    active_stages: list[Stage]
    orange_score: float = Field(ge=0, allow_inf_nan=False)
    yellow_score: float = Field(ge=0, allow_inf_nan=False)
    composite_ratio: float = Field(ge=0, allow_inf_nan=False)
    sort_value: float = Field(allow_inf_nan=False)
    manual_check: StrictBool
    manual_reasons: list[dict]
    breakdown: list[dict]
    signals: list[dict]
    level: Literal['Low', 'Medium', 'High', 'Critical']
    total_score: float = Field(ge=0, allow_inf_nan=False)
    combo_upgraded: StrictBool
    rules_digest: str = Field(min_length=1)
    expected_state_version: int = Field(ge=0, strict=True)
    corp_code: str = Field(min_length=1)
    as_of: str


@dataclass(frozen=True)
class UnifiedBindings:
    classify: Callable[[str], list[dict]]
    prepare_signals: Callable[[dict], list[dict]]
    # Check server-side CURRENT assessment, tenant and ownership before mutation.
    ensure_current: Callable[[dict], Awaitable[bool]]
    # Must persist request/time usage before another attempt, if provided.
    record_budget: Callable[[dict], Awaitable[None]] | None = None
    can_publish_partial: Callable[[dict], bool] | None = None


async def ensure_current(state, deps):
    if deps.unified is None:
        return
    context = state.get('execution_context')
    if not context or context.get('kind') != 'review':
        raise PermissionError('조직별 검토 context가 필요합니다')
    if await deps.unified.ensure_current({
        'context': dict(context), 'assessment_id': state.get('assessment_id'),
        'rcept_no': state['disclosure']['rcept_no'],
        'customer_ids': [c['customer_id'] for c in state.get('affected_customers', [])],
    }) is not True:
        raise PermissionError('현재 판정·조직·고객 소유권 확인 실패')
