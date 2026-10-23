"""R2 Phase 1 boundaries. Rule/financial implementations are supplied by R3/R4.

No score, threshold or financial calculation is implemented here. See the
pending integration contract at docs/contracts/agent.md before wiring real tools.
"""
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from pydantic import Field, StrictBool, StrictStr, field_validator, model_validator
import re

from agent.state import Evidence, StrictModel

Stage = Literal["RED", "ORANGE", "YELLOW", "COMPOSITE", "WATCH", "NONE"]
ALERT_STAGES = frozenset({"RED", "ORANGE", "YELLOW", "COMPOSITE"})


class RiskRoute(StrictModel):
    bucket: Literal["RED", "ORANGE", "YELLOW"]
    item: str = Field(min_length=1)
    text_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def nonempty_unique_names(self):
        if not self.item.strip() or any(not f.strip() for f in self.text_flags):
            raise ValueError("빈 위험 항목·플래그")
        if len(self.text_flags) != len(set(self.text_flags)):
            raise ValueError("중복 플래그")
        return self


class RiskFlag(StrictModel):
    item: str = Field(min_length=1)
    flag: str = Field(min_length=1)
    value: StrictBool | StrictStr | None
    evidence: list[Evidence] = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def text_not_numeric_indicator(cls, value):
        if isinstance(value, str) and (not value.strip() or re.fullmatch(r"[\d\s.,+%−-]+", value)):
            raise ValueError("수치 지표는 AI 텍스트 플래그가 아닙니다")
        return value


class FlagsOutput(StrictModel):
    flags: list[RiskFlag]

    @model_validator(mode="after")
    def unique_flags(self):
        keys = [(f.item, f.flag) for f in self.flags]
        if len(keys) != len(set(keys)):
            raise ValueError("같은 항목·플래그 중복")
        return self


class StageResult(StrictModel):
    stage: Stage
    orange_score: float = Field(ge=0, allow_inf_nan=False, strict=True)
    yellow_score: float = Field(ge=0, allow_inf_nan=False, strict=True)
    combined_ratio: float = Field(ge=0, allow_inf_nan=False, strict=True)
    breakdown: list[dict] = Field(default_factory=list)
    signals: list[dict] = Field(default_factory=list)
    needs_manual_check: StrictBool = False


@dataclass(frozen=True)
class Phase1Bindings:
    """Explicit opt-in; async bindings wrap R4 MCP tools, never LLM decisions.

    classify(title) is an approved title/API mapping supplied at assembly time.
    Rule methods receive dict payloads and return dicts. The final method returns
    StageResult's shape. Persistence uses Dependencies.persistence's existing port.
    """
    classify: Callable[[str], list[dict]]
    mark_red: Callable[[dict], Awaitable[dict]]
    score_orange: Callable[[dict], Awaitable[dict]]
    financial_snapshot: Callable[[dict], Awaitable[dict]]
    score_yellow: Callable[[dict], Awaitable[dict]]
    judge_stage: Callable[[dict], Awaitable[dict]]
