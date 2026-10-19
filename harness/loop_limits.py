"""Harness 5 — Agent Loop: 반복 한도.

최대 8턴, 작업당 120초, 같은 도구는 3회까지. 넘으면 LoopLimitExceeded →
호출한 쪽(노드/그래프)이 status를 MANUAL_CHECK로 저장하고 종료한다.
"""
import time
from dataclasses import dataclass, field

MAX_TURNS = 8
MAX_SECONDS = 120
MAX_SAME_TOOL = 3


class LoopLimitExceeded(Exception):
    """한도 초과. reason에 어떤 한도인지 담는다."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def count_tool_call(counts: dict[str, int] | None, tool_name: str, max_same: int = MAX_SAME_TOOL) -> dict[str, int]:
    """State.tool_call_counts에 호출 1회를 더한 새 dict를 돌려준다. 한도를 넘으면 예외."""
    new = dict(counts or {})
    new[tool_name] = new.get(tool_name, 0) + 1
    if new[tool_name] > max_same:
        raise LoopLimitExceeded(f"도구 '{tool_name}' 호출 {new[tool_name]}회 (한도 {max_same}회)")
    return new


@dataclass
class LoopGuard:
    """AI 노드 안의 도구 호출 루프에서 매 턴마다 check()를 부른다."""

    max_turns: int = MAX_TURNS
    max_seconds: float = MAX_SECONDS
    max_same_tool: int = MAX_SAME_TOOL
    started_at: float = field(default_factory=time.monotonic)
    turns: int = 0
    tool_counts: dict[str, int] = field(default_factory=dict)

    def check(self) -> None:
        """턴 1회를 소비한다. 턴 수나 시간이 한도를 넘으면 예외."""
        self.turns += 1
        if self.turns > self.max_turns:
            raise LoopLimitExceeded(f"턴 {self.turns}회 (한도 {self.max_turns}회)")
        elapsed = time.monotonic() - self.started_at
        if elapsed > self.max_seconds:
            raise LoopLimitExceeded(f"작업 {elapsed:.0f}초 (한도 {self.max_seconds:.0f}초)")

    def tool_call(self, tool_name: str) -> None:
        self.tool_counts = count_tool_call(self.tool_counts, tool_name, self.max_same_tool)
