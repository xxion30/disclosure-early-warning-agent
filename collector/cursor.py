"""증분 조회 범위 계산 (순수 함수). 커서의 저장·예약은 R4의 shared/collection.py가 한다.

계약은 docs/contracts/collector.md 3-2:
- `collect_cursors.last_success_at` = 그 종목의 조회 범위를 모든 페이지까지 저장하고 끝낸 시각(시간대 포함).
- 다음 조회 시작일 = `last_success_at`을 KST로 바꾼 날짜 − 1일. list.json의 접수일(rcept_dt)이 날짜까지만 있어서,
  같은 날 늦게 접수된 공시를 놓치지 않도록 하루 겹쳐 받고 접수번호로 중복을 버린다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def incremental_start(last_success_at: str | None, today: date) -> date | None:
    """커서가 없으면 None(백필이 필요하다). 있으면 조회 시작일."""
    if not last_success_at:
        return None
    last = datetime.fromisoformat(last_success_at)
    if last.tzinfo is None:  # R4의 finish_stock이 시간대 없는 값을 거절하므로 정상이면 오지 않는다. 오면 KST로 본다
        last = last.replace(tzinfo=KST)
    return min(last.astimezone(KST).date() - timedelta(days=1), today)  # 시계가 앞서 있어도 미래 날짜를 조회하지 않는다
