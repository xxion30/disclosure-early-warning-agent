"""Small integration boundaries; implementations are explicitly injected."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol

from pydantic import BaseModel
from harness.loop_limits import MAX_SECONDS, MAX_TURNS
from shared.schemas import ToolResponse
from agent.state import AgentState

if TYPE_CHECKING:
    from agent.phase1 import Phase1Bindings
    from agent.integration import UnifiedBindings


class AgentError(Exception):
    def __init__(self, message: str, status: str = "MANUAL_CHECK", *, retryable=False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class Tools(Protocol):
    async def call(self, name: str, arguments: dict) -> ToolResponse: ...


class Model(Protocol):
    async def generate(self, prompt: str, schema: type[BaseModel], payload: dict) -> BaseModel: ...


class Harness(Protocol):
    async def prepare_input(self, payload: dict) -> dict:
        """Mask PII, trim model context and enforce the 6,000-token budget."""
        ...
    async def check_message(self, message: str) -> list[str]: ...
    async def recover_llm(self, primary: Callable, fallback: Callable) -> Any: ...
    async def recover_tool(self, operation: Callable) -> ToolResponse: ...
    async def event(self, name: str, payload: dict) -> None: ...


class Persistence(Protocol):
    async def save(self, state: AgentState, status: str) -> int:
        """Upsert by rcept_no, preserving assessment_id on status updates."""
        ...
    async def audit(self, state: AgentState, action: str, payload: dict) -> None:
        """Durable, idempotent record including trace_id and customer decisions."""
        ...


@dataclass(frozen=True)
class Dependencies:
    tools: Tools
    model: Model
    harness: Harness
    persistence: Persistence
    classify: Callable[[str], str | None]
    # Must map verified facts to B's agreed new_signals schema without assigning scores.
    signal_input: Callable[[dict], list[dict]]
    # Authorizes actual task creation; must also implement storage idempotency.
    create_task: Callable[[dict], Awaitable[int]]
    # 신호가 없는 것이 정상인 disclosure_type (rules.yaml no_signal_ok_types). 비어 있으면 모든 유형이 사실을 요구한다.
    quiet_types: frozenset[str] = frozenset()
    execution_seconds: float = MAX_SECONDS  # harness/loop_limits.py가 한도의 단일 출처
    max_model_turns: int = MAX_TURNS
    # R2's opt-in Phase 1 graph. Real bindings/persistence need R4 agreement.
    phase1: Phase1Bindings | None = None
    # New unified tool path; legacy Phase1Bindings remains opt-in compatible.
    unified: UnifiedBindings | None = None
    # Hosted public execution returns a candidate; R4 publishes under its lease.
    defer_publication: bool = False
