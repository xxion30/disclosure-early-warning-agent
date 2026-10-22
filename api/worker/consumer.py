"""큐 소비자 — 큐에서 꺼낸 공시를 RunManager로 실행하고, 실패하면 다시 시도하거나 DLQ로 보낸다.

API 프로세스 안에서 돈다 (api/main.py lifespan). 그래야
- SSE 진행 이벤트가 그대로 화면에 간다 (RunManager가 같은 프로세스)
- AgentRuntime이 하나뿐이다 (docs/contracts/agent.md: 초기 데모는 단일 worker 권장)

재시도 기준:
- 실행 기록(체크포인트)이 생기기 전에 실패 → 일시 장애(MCP 서버 꺼짐, 조립 실패 등)로 보고 지연 후 다시 시도
- 실행 기록이 남은 실패(MANUAL_CHECK·FAILED) → 같은 thread를 다시 돌려도 결과가 같다. 사람이 볼 일이라 DLQ
- done → 성공
"""
from __future__ import annotations

import asyncio
import logging

from api.graph_adapter import FAILED, GraphAdapter, now_iso
from api.run_manager import RunConflict, RunManager
from api.worker.queue import DLQ, RETRY_QUEUE, QueueBackend

log = logging.getLogger("api.worker")

MAX_ATTEMPTS = 3
RETRY_DELAYS = (3, 10)  # 2번째, 3번째 시도 전 대기(초)


class DisclosureConsumer:
    def __init__(self, manager: RunManager, adapter: GraphAdapter, backend: QueueBackend,
                 retry_delays: tuple[float, ...] = RETRY_DELAYS, max_attempts: int = MAX_ATTEMPTS):
        self.manager = manager
        self.adapter = adapter
        self.backend = backend
        self.retry_delays = retry_delays
        self.max_attempts = max_attempts
        self.processed: list[tuple[str, int, str]] = []  # (rcept_no, attempt, 결과) — 확인·테스트용

    async def handle(self, body: dict) -> None:
        rcept_no = str(body["rcept_no"])
        attempt = int(body.get("attempt", 1))
        try:
            ch = self.manager.start(rcept_no)
        except RunConflict:
            ch = self.manager.get(rcept_no)
            if ch is not None and ch.running:
                await ch.task  # 화면에서 직접 실행한 것과 겹치면 그 실행이 끝날 때까지만 기다린다
            self._record(rcept_no, attempt, "skip")
            log.info("skip %s: 이미 실행 중이거나 처리됨", rcept_no)
            return

        await ch.task
        if ch.last_event != FAILED:
            self._record(rcept_no, attempt, ch.last_event or "?")
            return

        message = ch.history[-1]["data"].get("message", "")
        if await self._has_checkpoint(rcept_no):
            await self._dead_letter(rcept_no, attempt, "실행 기록이 남은 실패 — 수동 확인 필요", message)
        elif attempt < self.max_attempts:
            delay = self.retry_delays[min(attempt - 1, len(self.retry_delays) - 1)]
            await self.backend.publish(RETRY_QUEUE, {"rcept_no": rcept_no, "attempt": attempt + 1,
                                                     "last_error": message}, delay_seconds=delay)
            self._record(rcept_no, attempt, f"retry in {delay}s")
            log.warning("retry %s (%d/%d) in %ss: %s", rcept_no, attempt + 1, self.max_attempts, delay, message)
        else:
            await self._dead_letter(rcept_no, attempt, f"{self.max_attempts}회 시도 실패", message)

    async def _has_checkpoint(self, rcept_no: str) -> bool:
        try:
            return await self.adapter.get_state(rcept_no) is not None
        except Exception:  # 의존성 조립 실패 등 — 상태를 못 읽으면 일시 장애로 본다
            return False

    async def _dead_letter(self, rcept_no: str, attempt: int, reason: str, error: str) -> None:
        await self.backend.publish(DLQ, {"rcept_no": rcept_no, "attempt": attempt, "reason": reason,
                                         "error": error, "failed_at": now_iso()})
        self._record(rcept_no, attempt, "dlq")
        log.error("dlq %s after %d attempt(s): %s — %s", rcept_no, attempt, reason, error)

    def _record(self, rcept_no: str, attempt: int, result: str) -> None:
        self.processed.append((rcept_no, attempt, result))

    async def run_forever(self) -> None:
        """연결이 끊기면 aio-pika robust 연결이 다시 붙는다. 처리 중 예외는 메시지를 다시 넣고 계속한다."""
        while True:
            try:
                await self.backend.consume(self.handle)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("consumer 오류, 3초 뒤 다시 시작")
                await asyncio.sleep(3)
