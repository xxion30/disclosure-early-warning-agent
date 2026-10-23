"""종목별 수집 작업과 수집 실행(run) 한 번. 계약은 docs/contracts/collector.md 3장.

흐름 (종목 1개): 회사 upsert → 조회 범위(커서 하루 전 또는 백필) → list.json → 공시 upsert → 정기보고서마다 재무제표·지표 저장 → 커서 전진.
커서는 모든 페이지·저장이 끝난 뒤에만 전진한다. 실패하면 `success_at` 없이 예약만 풀어 커서를 그대로 둔다 (R4 `finish_stock`).

실행(run)의 생성·예약·임대·종료는 R4의 shared/collection.py가 한다. R4의 `POST /collect/run`은 `PENDING` 실행을 만들고 종목을 예약한 뒤
`app.state.collect_dispatcher(run_id, stock_codes)`를 부른다 → 그 자리에 `dispatch_collection`을 꽂으면 된다 (연결은 R4).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import select

from collector import repo
from collector.backfill import backfill_start
from collector.cursor import incremental_start, now_kst
from collector.dart_client import DartClient
from collector.indicators import compute_audit_opinion, compute_financial_indicators
from collector.reports import PeriodicReport, parse_periodic_title
from collector.watchlist import CorpCodeBook, Target, build_watchlist, resolve
from shared.collection import (
    finish_run,
    finish_stock,
    heartbeat_run,
    lease_deadline,
    recover_expired_runs,
    release_run,
    reserve_stocks,
)
from shared.config import get_settings
from shared.db import get_session
from shared.models import CollectCursor, CollectRun, FinancialSnapshot

log = logging.getLogger("collector")

BASIS = {"CFS": "CONSOLIDATED", "OFS": "SEPARATE"}  # 연결·개별 둘 다 저장한다 (R1 결정)
AUDIT_API = "accnutAdtorNmNdAdtOpinion"
DEFAULT_CONCURRENCY = 3


@dataclass
class StockOutcome:
    stock_code: str
    ok: bool
    new_disclosures: int = 0
    snapshots: int = 0
    error: str | None = None


@dataclass
class RunSummary:
    run_id: str
    outcomes: list[StockOutcome] = field(default_factory=list)

    @property
    def failed(self) -> list[StockOutcome]:
        return [o for o in self.outcomes if not o.ok]


def _error_text(e: Exception) -> str:
    # DartError·httpx 오류는 키가 든 URL을 이미 지운 메시지다 (dart_client). 그 밖의 오류도 길이만 줄여 남긴다
    return f"{type(e).__name__}: {e}"[:300]


# ---- 종목 1개 ----
def _covered(session, corp_code: str, report: PeriodicReport, list_rcept_no: str) -> bool:
    """이 보고서(또는 더 나중 정정본)의 스냅샷이 이미 있으면 True. DART는 정정본 중 최신 값만 주므로 접수번호가 같거나 더 크면 충분하다."""
    have = session.scalars(select(FinancialSnapshot.rcept_no).where(
        FinancialSnapshot.corp_code == corp_code, FinancialSnapshot.period == report.period))
    return any(r >= list_rcept_no for r in have)


async def _collect_report(client: DartClient, target: Target, report: PeriodicReport, fetched_at: str) -> list[dict]:
    """정기보고서 1건 → 저장할 스냅샷 목록 [{basis, rcept_no, indicators, sources}]. 연결이 먼저."""
    out: list[dict] = []
    for fs_div, basis in BASIS.items():
        rows = await client.financial_statements(target.corp_code, report.bsns_year, report.reprt_code, fs_div)
        if not rows:  # 013: 그 구분의 재무제표가 없다 (정상)
            continue
        rcept_no = rows[0].get("rcept_no")  # 정정본이 있으면 API는 최신 정정본의 접수번호를 준다 (확인됨)
        res = compute_financial_indicators(rows, report.period_kind)
        out.append({"basis": basis, "rcept_no": rcept_no, "indicators": dict(res.indicators),
                    "sources": [{"api": "fnlttSinglAcntAll", "fs_div": fs_div, "rcept_no": rcept_no,
                                 "fetched_at": fetched_at, "accounts": res.accounts}]})
    if out and report.period_kind == "ANNUAL":
        # 감사의견은 연결·개별을 구분해 주지 않으므로 먼저 있는 쪽(연결)에 붙인다. R4는 같은 기간에서 연결을 먼저 고른다
        audit = compute_audit_opinion(await client.periodic_report(AUDIT_API, target.corp_code, report.bsns_year, "11011"))
        out[0]["indicators"].update(audit.indicators)
        out[0]["sources"].append({"api": AUDIT_API, "fetched_at": fetched_at, "accounts": audit.accounts})
    return out


async def collect_stock(client: DartClient, target: Target, *, today: date, now: datetime | None = None) -> StockOutcome:
    """종목 1개를 수집한다. 예외는 밖으로 던지지 않고 실패 결과로 돌려준다."""
    now = now or now_kst()
    try:
        with get_session() as s:
            repo.upsert_company(s, target)
            cursor = s.get(CollectCursor, target.stock_code)
            last_success_at = cursor.last_success_at if cursor else None
        start = incremental_start(last_success_at, today) or backfill_start(today)
        rows = await client.list_disclosures(target.corp_code, start, today)
        with get_session() as s:
            new = repo.upsert_disclosures(s, target.corp_code, rows)
        saved = 0
        for row in sorted(rows, key=lambda r: r["rcept_no"]):
            report = parse_periodic_title(row.get("report_nm"))
            if report is None:
                continue
            with get_session() as s:
                if _covered(s, target.corp_code, report, row["rcept_no"]):
                    continue
            snapshots = await _collect_report(client, target, report, now.isoformat())
            with get_session() as s:
                saved += sum(repo.save_snapshot(s, target.corp_code, report, **snap) for snap in snapshots)
        return StockOutcome(target.stock_code, True, len(new), saved)
    except Exception as e:  # noqa: BLE001 — 한 종목의 실패가 다른 종목을 막지 않는다
        log.warning("수집 실패 %s: %s", target.stock_code, _error_text(e))
        return StockOutcome(target.stock_code, False, error=_error_text(e))


# ---- 실행(run) 1회 ----
@dataclass
class _State:
    expired: bool = False


async def _heartbeat(run_id: str, interval: float, state: _State) -> None:
    while True:
        await asyncio.sleep(interval)
        with get_session() as s:
            if not heartbeat_run(s, run_id):
                state.expired = True
                return


def _abort_run(run_id: str, message: str) -> None:
    """실행이 시작하지도 못하고 죽었을 때: 예약을 풀고 실패로 닫는다 (예약이 남으면 그 종목이 계속 잠긴다)."""
    with get_session() as s:
        release_run(s, run_id)
        row = s.get(CollectRun, run_id)
        if row is not None and row.status in ("PENDING", "RUNNING"):
            row.status, row.finished_at, row.error = "FAILED", datetime.now(timezone.utc).isoformat(), message[:500]


async def run_collection(
    run_id: str, stock_codes: list[str], *, client: DartClient | None = None, book: CorpCodeBook | None = None,
    concurrency: int = DEFAULT_CONCURRENCY, today: date | None = None,
) -> RunSummary:
    """R4가 만들고 예약해 둔 실행(run_id)을 처리한다. 모든 예약 종목을 성공 또는 실패로 풀어 주고 실행을 닫는다.

    `fetched_count`에는 **성공한 종목 수**를 넣는다. R4의 `finish_run`이 'fetched 0 + 실패 ≥ 1'을 FAILED로 보기 때문에,
    새 공시 수를 넣으면 조용한 날의 일부 실패가 PARTIAL이 아니라 FAILED로 보인다 (R4 확인 필요).
    """
    summary = RunSummary(run_id)
    try:
        async with AsyncExitStack() as stack:
            c = client or await stack.enter_async_context(DartClient.from_settings())
            with get_session() as s:
                if not heartbeat_run(s, run_id):  # 없거나 이미 끝났거나 임대가 만료됨
                    log.warning("수집 실행을 시작하지 못함(없음·종료·만료): %s", run_id)
                    return summary
            targets = {t.stock_code: t for t in resolve(stock_codes, await (book or CorpCodeBook()).get(c)).targets}
            state = _State()
            beat = asyncio.create_task(_heartbeat(run_id, max(1.0, get_settings().COLLECTION_LEASE_SECONDS / 3), state))
            sem = asyncio.Semaphore(concurrency)
            today_ = today or now_kst().date()

            async def one(stock: str) -> StockOutcome:
                async with sem:
                    if state.expired:
                        out = StockOutcome(stock, False, error="수집 실행 임대가 만료됨")
                    elif stock not in targets:
                        out = StockOutcome(stock, False, error="corpCode.xml에 없는 종목")
                    else:
                        out = await collect_stock(c, targets[stock], today=today_)
                try:  # 성공한 종목만 커서를 전진시킨다
                    with get_session() as s:
                        finish_stock(s, run_id, stock, success_at=now_kst().isoformat() if out.ok else None)
                except Exception as e:  # noqa: BLE001 — 커서를 못 옮기면 성공으로 치지 않는다
                    out = StockOutcome(stock, False, out.new_disclosures, out.snapshots, _error_text(e))
                    with get_session() as s:
                        finish_stock(s, run_id, stock)
                return out

            try:
                summary.outcomes = list(await asyncio.gather(*(one(s) for s in stock_codes)))
            finally:
                beat.cancel()
            ok_count = sum(o.ok for o in summary.outcomes)
            errors = "; ".join(f"{o.stock_code}: {o.error}" for o in summary.failed)
            with get_session() as s:
                if not finish_run(s, run_id, fetched_count=ok_count, failed_count=len(summary.failed), error=errors or None):
                    log.warning("수집 실행을 정상 종료하지 못함(만료·이미 종료): %s", run_id)
    except Exception as e:  # noqa: BLE001
        log.exception("수집 실행 중 예기치 않은 오류: %s", run_id)
        _abort_run(run_id, _error_text(e))
    return summary


_background: set[asyncio.Task] = set()


async def dispatch_collection(run_id: str, stock_codes: list[str]) -> None:
    """R4 API의 `collect_dispatcher` 자리에 꽂는 함수. 실행을 백그라운드로 시작하고 바로 돌아온다 (API는 202를 돌려줘야 한다)."""
    task = asyncio.create_task(run_collection(run_id, list(stock_codes)))
    _background.add(task)
    task.add_done_callback(_background.discard)


# ---- 정기 수집(전체 보유 종목, 고객사 구분 없음) ----
def create_run(session, stock_codes: list[str], *, requested_by: str = "collector") -> tuple[str, list[str]] | None:
    """전체(고객사 구분 없는) 수집 실행을 만들고 종목을 예약한다. 이미 돌고 있거나 예약할 종목이 없으면 None.

    R4 `POST /collect/run`과 같은 순서다. 호출한 쪽이 세션을 커밋해야 run을 `run_collection`이 볼 수 있다.
    """
    recover_expired_runs(session)
    if session.scalar(select(CollectRun.run_id).where(CollectRun.tenant_id.is_(None), CollectRun.status.in_(["PENDING", "RUNNING"]))):
        return None
    run = CollectRun(run_id=str(uuid.uuid4()), tenant_id=None, requested_by=requested_by, stock_codes=sorted(set(stock_codes)),
                     status="PENDING", started_at=datetime.now(timezone.utc).isoformat(), lease_expires_at=lease_deadline())
    session.add(run)
    session.flush()
    reserved = reserve_stocks(session, run.run_id, run.stock_codes)
    if not reserved:
        session.rollback()
        return None
    run.stock_codes = reserved
    session.flush()
    return run.run_id, reserved


async def collect_once(*, client: DartClient | None = None, book: CorpCodeBook | None = None) -> RunSummary | None:
    """보유 종목 합집합을 읽어 한 번 수집한다 (매일 09:05 스케줄러와 `python -m collector`가 부른다)."""
    book = book or CorpCodeBook()
    async with AsyncExitStack() as stack:
        c = client or await stack.enter_async_context(DartClient.from_settings())
        with get_session() as s:
            watch = await build_watchlist(s, c, book)
        if watch.skipped:
            log.info("수집 대상에서 제외: %s", ", ".join(f"{k.stock_code}({k.reason})" for k in watch.skipped))
        with get_session() as s:
            for t in watch.targets:  # 공시·API(R4)가 companies를 가리키므로 먼저 넣는다
                repo.upsert_company(s, t)
            created = create_run(s, [t.stock_code for t in watch.targets])
        if created is None:
            log.warning("수집을 시작하지 않음: 이미 실행 중이거나 예약할 종목이 없다")
            return None
        run_id, reserved = created
        return await run_collection(run_id, reserved, client=c, book=book)
