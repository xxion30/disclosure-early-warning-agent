"""Graph 실행을 백그라운드로 돌리고, 이벤트를 SSE 구독자들에게 나눠 준다.

- 공시 1건(rcept_no)마다 채널 하나. 실행 구간(처음 실행, 재개 1회)마다 이벤트 기록을 새로 쌓는다.
- 늦게 붙은 구독자도 현재 구간의 이벤트를 처음부터 다시 받는다 (POST /runs 직후 SSE 연결이 늦어도 안전).
- 메모리에만 있으므로 서버를 재시작하면 기록은 사라진다. 상태 자체는 Graph 체크포인트에 남는다.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from api.graph_adapter import FAILED, QUEUED, TERMINAL_EVENTS, Event, GraphAdapter, make_event

_CLOSE = object()  # 구독자 큐에 넣는 "구간 끝" 표시


class RunConflict(Exception):
    """이미 실행 중이거나 처리된 공시."""


@dataclass
class RunChannel:
    rcept_no: str
    history: list[Event] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    @property
    def last_event(self) -> str | None:
        return self.history[-1]["event"] if self.history else None

    def publish(self, event: Event) -> None:
        self.history.append(event)
        for q in self.subscribers:
            q.put_nowait(event)

    def close_segment(self) -> None:
        for q in self.subscribers:
            q.put_nowait(_CLOSE)


class RunManager:
    def __init__(self, adapter: GraphAdapter):
        self.adapter = adapter
        self._channels: dict[str, RunChannel] = {}

    def get(self, rcept_no: str) -> RunChannel | None:
        return self._channels.get(rcept_no)

    def start(self, rcept_no: str) -> RunChannel:
        """처음 실행, 실패 뒤 다시 실행, 큐에서 꺼낸 실행. 이미 실행 중·처리된 공시는 RunConflict."""
        ch = self._channels.get(rcept_no)
        if ch is not None and (ch.running or ch.last_event not in (FAILED, QUEUED)):
            raise RunConflict(f"{rcept_no}는 이미 실행 중이거나 처리되었습니다.")
        if ch is None:
            ch = self._channels[rcept_no] = RunChannel(rcept_no)
        ch.history = []  # 새 구간. 큐 대기 중에 붙은 SSE 구독자는 그대로 이어서 받는다
        self._spawn(ch, self.adapter.run(rcept_no))
        return ch

    def mark_queued(self, rcept_no: str, position: int | None = None) -> RunChannel:
        """큐 모드(POST /runs): 실행 전에 채널을 만들어 두어 SSE를 바로 붙일 수 있게 한다."""
        ch = self._channels.get(rcept_no)
        if ch is not None and (ch.running or ch.last_event not in (FAILED, QUEUED)):
            raise RunConflict(f"{rcept_no}는 이미 실행 중이거나 처리되었습니다.")
        if ch is None:
            ch = self._channels[rcept_no] = RunChannel(rcept_no)
        ch.history = []
        ch.publish(make_event(QUEUED, rcept_no, position=position))
        return ch

    def _spawn(self, ch: RunChannel, events: AsyncIterator[Event]) -> None:
        ch.task = asyncio.create_task(self._pump(ch, events), name=f"run-{ch.rcept_no}")

    async def _pump(self, ch: RunChannel, events: AsyncIterator[Event]) -> None:
        try:
            async for ev in events:
                ch.publish(ev)
                if ev["event"] in TERMINAL_EVENTS:
                    break
            else:
                if ch.last_event not in TERMINAL_EVENTS:
                    ch.publish(make_event(FAILED, ch.rcept_no, message="Graph가 결과 없이 끝났습니다."))
        except Exception as exc:
            ch.publish(make_event(FAILED, ch.rcept_no, message=f"{type(exc).__name__}: {exc}"))
        finally:
            ch.close_segment()

    async def subscribe(self, rcept_no: str) -> AsyncIterator[Event | None]:
        """현재 구간의 지난 이벤트 → 새 이벤트 순으로 내보낸다. 오래 조용하면 None(keep-alive)."""
        ch = self._channels[rcept_no]
        q: asyncio.Queue = asyncio.Queue()
        # 복사와 등록 사이에 await가 없으므로 빠지거나 겹치는 이벤트가 없다
        backlog = list(ch.history)
        finished = ch.last_event in TERMINAL_EVENTS
        if not finished:
            ch.subscribers.add(q)
        try:
            for ev in backlog:
                yield ev
            if finished:
                return
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield None
                    continue
                if item is _CLOSE:
                    return
                yield item
        finally:
            ch.subscribers.discard(q)


_manager: RunManager | None = None


def get_run_manager() -> RunManager:
    global _manager
    if _manager is None:
        from api.graph_adapter import get_graph_adapter

        _manager = RunManager(get_graph_adapter())
    return _manager
