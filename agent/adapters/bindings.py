"""Bind D's functions without assuming not-yet-merged module signatures."""
from dataclasses import dataclass
from typing import Awaitable, Callable, Any


@dataclass(frozen=True)
class HarnessBindings:
    prepare_input: Callable[[dict], Awaitable[dict]]
    check_message: Callable[[str], Awaitable[list[str]]]
    recover_llm: Callable[[Callable, Callable], Awaitable[Any]]
    recover_tool: Callable[[Callable], Awaitable[Any]]
    event: Callable[[str, dict], Awaitable[None]]
