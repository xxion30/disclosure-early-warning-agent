"""Small integration boundaries; implementations are explicitly injected."""
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from pydantic import BaseModel
from shared.schemas import ToolResponse
from agent.state import AgentState


class AgentError(Exception):
    def __init__(self, message: str, status: str = "MANUAL_CHECK"):
        super().__init__(message)
        self.status = status


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
    execution_seconds: float = 120
    max_model_turns: int = 8
