"""스케줄러·진입점 테스트: 시계와 sleep을 주입해 기다리지 않고 시험한다."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from collector import __main__ as entry
from collector.cursor import KST
from collector.jobs import RunSummary, StockOutcome
from collector.scheduler import next_run_at, run_daily


def kst(*args):
    return datetime(*args, tzinfo=KST)


@pytest.mark.parametrize("now, expected", [
    (kst(2026, 10, 23, 8, 0), kst(2026, 10, 23, 9, 5)),  # 아직 전이면 오늘
    (kst(2026, 10, 23, 9, 5, 1), kst(2026, 10, 24, 9, 5)),  # 지났으면 내일
    (kst(2026, 10, 23, 9, 5, 0), kst(2026, 10, 24, 9, 5)),  # 정확히 그 시각이면 같은 날 두 번 돌지 않는다
    (kst(2026, 12, 31, 23, 59), kst(2027, 1, 1, 9, 5)),  # 해를 넘긴다
    (datetime(2026, 10, 22, 23, 30, tzinfo=timezone.utc), kst(2026, 10, 23, 9, 5)),  # UTC 23:30 = KST 08:30 → 오늘 09:05
])
def test_next_run_at(now, expected):
    assert next_run_at(now) == expected


def test_run_daily_waits_until_next_slot_then_runs_each_day_and_survives_failures():
    clock_now = [kst(2026, 10, 23, 9, 0)]
    sleeps, calls = [], []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        clock_now[0] += timedelta(seconds=seconds)  # 기다린 만큼 시계가 간다

    async def job():
        calls.append(clock_now[0])
        if len(calls) == 1:
            raise RuntimeError("첫날 실패")  # 스케줄러가 죽으면 안 된다

    asyncio.run(run_daily(job, "09:05", max_runs=3, sleep=fake_sleep, clock=lambda: clock_now[0]))
    assert sleeps == [300.0, 86400.0, 86400.0]  # 09:00 → 09:05, 이후 하루씩
    assert calls == [kst(2026, 10, 23, 9, 5), kst(2026, 10, 24, 9, 5), kst(2026, 10, 25, 9, 5)]


def summary(*outcomes):
    return RunSummary("run-1", list(outcomes))


def test_once_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(entry, "init_db", lambda: None)
    ok, bad = StockOutcome("001570", True, 3, 2), StockOutcome("000660", False, error="DartUnavailable: 시간 초과")

    async def all_ok():
        return summary(ok)

    async def partial():
        return summary(ok, bad)

    async def not_started():
        return None

    for fn, code in [(all_ok, 0), (partial, 1), (not_started, 2)]:
        monkeypatch.setattr(entry, "collect_once", fn)
        assert entry.main(["--once"]) == code
    out = capsys.readouterr().out
    assert "새 공시 3건" in out and "000660 FAIL" in out and "시작하지 않았습니다" in out


def test_requires_exactly_one_mode(monkeypatch):
    monkeypatch.setattr(entry, "init_db", lambda: None)
    with pytest.raises(SystemExit):
        entry.main([])
    with pytest.raises(SystemExit):
        entry.main(["--once", "--schedule"])
