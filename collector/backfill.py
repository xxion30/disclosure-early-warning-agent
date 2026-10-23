"""새 종목 백필 범위: 보유 목록에 처음 들어온 종목은 최근 12개월 공시를 한 번에 받아 채운다 (collector.md 3-5).

`corp_code`를 지정하면 16개월을 한 번에 조회할 수 있다(확인됨)고 해서 구간을 쪼개지 않는다.
백필이 끝나야 커서(`last_success_at`)가 전진하므로, 중간에 실패하면 다음 실행이 처음부터 다시 한다 (저장은 upsert).
"""
from __future__ import annotations

import calendar
from datetime import date

BACKFILL_MONTHS = 12  # 설정 COLLECT_BACKFILL_MONTHS가 추가되면 그 값을 쓴다 (설정 추가는 R4)


def backfill_start(today: date, months: int = BACKFILL_MONTHS) -> date:
    """today에서 months개월 전. 그 달에 같은 날이 없으면 말일(예: 3월 31일 − 1개월 = 2월 말일)."""
    index = today.year * 12 + (today.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))
