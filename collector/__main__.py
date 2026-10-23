"""수집기 실행 진입점.

    uv run python -m collector --once       # 지금 한 번 수집하고 끝낸다 (점검·수동 실행)
    uv run python -m collector --schedule   # 매일 09:05(KST)에 수집한다. 계속 돈다 (collector 프로세스)

DART_API_KEY가 .env에 있어야 한다. 이미 전체 수집이 돌고 있으면 새로 시작하지 않는다.
종료 코드: 0 성공, 1 일부 또는 전체 실패, 2 시작하지 않음(이미 실행 중이거나 수집할 종목 없음).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from collector.jobs import collect_once
from collector.scheduler import configured_time, run_daily
from shared.db import init_db


async def _once() -> int:
    summary = await collect_once()
    if summary is None:
        print("수집을 시작하지 않았습니다 (이미 실행 중이거나 수집할 종목이 없음).")
        return 2
    ok = sum(o.ok for o in summary.outcomes)
    print(f"실행 {summary.run_id}: 성공 {ok}종목, 실패 {len(summary.failed)}종목")
    for o in summary.outcomes:
        print(f"  {o.stock_code} {'OK ' if o.ok else 'FAIL'} 새 공시 {o.new_disclosures}건, 재무 스냅샷 {o.snapshots}개" + (f" — {o.error}" if o.error else ""))
    return 1 if summary.failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="collector", description="DART 수집기")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="지금 한 번 수집")
    mode.add_argument("--schedule", action="store_true", help="매일 정해진 시각(기본 09:05 KST)에 수집")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")  # 키는 로그에서 가려진다 (dart_client)
    init_db()
    if args.once:
        return asyncio.run(_once())
    print(f"매일 {configured_time()}(KST)에 수집합니다. 종료는 Ctrl+C.")
    try:
        asyncio.run(run_daily(lambda: collect_once()))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
