"""매일 09:05(KST)에 수집을 한 번 돌린다. 하루 한 번이라 APScheduler 없이 asyncio로 다음 실행 시각까지 기다린다.

APScheduler는 설계서가 정한 도구지만 아직 의존성에 없다(추가는 R4). 필요해지면 `run_daily` 한 곳만 바꾸면 된다.
설정 `COLLECT_TIME`이 shared/config.py에 추가되면 그 값을 쓰고, 없으면 09:05다.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from collector.cursor import KST, now_kst
from shared.config import get_settings

log = logging.getLogger("collector")
DEFAULT_TIME = "09:05"


def configured_time() -> str:
    return getattr(get_settings(), "COLLECT_TIME", DEFAULT_TIME)


def next_run_at(now: datetime, hhmm: str = DEFAULT_TIME) -> datetime:
    """`now` 이후 처음 오는 KST hhmm. 지금이 정확히 그 시각이면 다음 날이다 (같은 날 두 번 돌지 않게)."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    local = now.astimezone(KST)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > local else candidate + timedelta(days=1)


async def run_daily(
    job: Callable[[], Awaitable[object]], hhmm: str | None = None, *, max_runs: int | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, clock: Callable[[], datetime] = now_kst,
) -> None:
    """매일 hhmm(KST)에 job을 부른다. job이 예외를 던져도 로그만 남기고 다음 날 다시 돈다. max_runs는 테스트용."""
    hhmm = hhmm or configured_time()
    runs = 0
    while max_runs is None or runs < max_runs:
        now = clock()
        target = next_run_at(now, hhmm)
        log.info("다음 수집: %s", target.isoformat())
        await sleep((target - now).total_seconds())
        try:
            await job()
        except Exception:  # noqa: BLE001 — 하루 실패가 스케줄러를 죽이면 다음 날도 안 돈다
            log.exception("정기 수집 실패")
        runs += 1
