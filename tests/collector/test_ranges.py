"""조회 범위(cursor·backfill)와 정기보고서 제목 해석 테스트. 제목은 실제 목록에서 본 것이다."""
from datetime import date

import pytest

from collector.backfill import backfill_start
from collector.cursor import incremental_start
from collector.reports import PeriodicReport, parse_periodic_title


def test_no_cursor_means_backfill_needed():
    assert incremental_start(None, date(2026, 10, 23)) is None


def test_incremental_start_is_one_day_before_last_success_in_kst():
    assert incremental_start("2025-03-21T09:05:00+09:00", date(2025, 3, 22)) == date(2025, 3, 20)


def test_incremental_start_converts_utc_to_kst_date():
    # 2025-03-21 23:30 UTC = 2025-03-22 08:30 KST → 날짜는 22일, 시작은 21일
    assert incremental_start("2025-03-21T23:30:00+00:00", date(2025, 3, 22)) == date(2025, 3, 21)


def test_incremental_start_never_in_the_future():
    assert incremental_start("2026-12-31T09:05:00+09:00", date(2026, 10, 23)) == date(2026, 10, 23)


def test_naive_timestamp_is_treated_as_kst():
    assert incremental_start("2025-03-21T09:05:00", date(2025, 3, 22)) == date(2025, 3, 20)


@pytest.mark.parametrize("today, months, expected", [
    (date(2026, 10, 23), 12, date(2025, 10, 23)),
    (date(2026, 3, 31), 1, date(2026, 2, 28)),  # 말일로 맞춘다
    (date(2024, 3, 31), 1, date(2024, 2, 29)),  # 윤년
    (date(2026, 1, 15), 2, date(2025, 11, 15)),  # 해를 넘긴다
])
def test_backfill_start(today, months, expected):
    assert backfill_start(today, months) == expected


@pytest.mark.parametrize("title, expected", [
    ("사업보고서 (2024.12)", PeriodicReport(2024, "11011", "2024", "ANNUAL", False)),
    ("[기재정정]사업보고서 (2022.12)", PeriodicReport(2022, "11011", "2022", "ANNUAL", True)),
    ("분기보고서 (2024.03)", PeriodicReport(2024, "11013", "2024-Q1", "YTD", False)),
    ("반기보고서 (2024.06)", PeriodicReport(2024, "11012", "2024-Q2", "YTD", False)),
    ("분기보고서 (2024.09)", PeriodicReport(2024, "11014", "2024-Q3", "YTD", False)),
])
def test_parse_periodic_title(title, expected):
    assert parse_periodic_title(title) == expected


@pytest.mark.parametrize("title", [
    "주요사항보고서(유상증자결정)", "[기재정정]주요사항보고서(유상증자결정)", "감사보고서제출",
    "사업보고서 (2024.06)",  # 12월 결산이 아님 → Phase 1에서는 건너뜀
    "분기보고서 (2024.12)", "", None,
])
def test_non_standard_titles_are_not_periodic_reports(title):
    assert parse_periodic_title(title) is None
