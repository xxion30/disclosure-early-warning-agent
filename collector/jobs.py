"""종목별 수집 작업과 수집 실행(run) 한 번. 계약은 docs/contracts/collector.md 3장.

흐름 (종목 1개): 회사 upsert → 조회 범위(커서 하루 전 또는 백필) → list.json → 공시 upsert → 정기보고서마다 재무제표·지표 저장
→ 항목 API 구조화 사실 저장 → 분석 인계(mark_ready) → 커서 전진.
구조화 사실과 인계는 R4의 shared/facts.py(`store_facts`)·shared/analysis.py(`mark_ready`)를 쓴다. 인계되지 않은 공시(`analysis_ready_at` 없음)만
처리하므로 실패한 공시는 다음 실행이 다시 시도하고, 인계된 공시는 다시 건드리지 않는다.
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
from collector.dart_client import DartBadRequest, DartClient
from collector.facts import build_ambiguous_payload, build_payload, item_for, parse_original_date, split_title
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
from shared.analysis import mark_ready
from shared.config import get_settings
from shared.db import get_session
from shared.facts import store_facts
from shared.models import CollectCursor, CollectRun, Disclosure, FinancialSnapshot

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
    facts: int = 0  # 구조화 사실을 저장한 공시 수
    handed_off: int = 0  # 분석에 인계(mark_ready)한 공시 수
    problems: list[str] = field(default_factory=list)  # 사실을 만들지 못한 이유 (실패는 아님)


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


def _compact(iso: str) -> str:
    return iso.replace("-", "")


async def _resolve_facts(client: DartClient, target: Target, rows: list[dict], pending: set[str], fetched_at: str) -> tuple[dict, dict, list[str]]:
    """인계 전 공시 중 항목 API 대상의 구조화 사실을 만든다. 반환: ({접수번호: payload}, {정정 접수번호: 원공시 접수번호 후보}, 문제 목록).

    항목 API는 정정 체인의 최신본만 주고 기간 필터가 **체인의 최초 접수일**에 적용된다 (collector.md 3-3).
    그래서 정정 공시는 본문의 `최초제출일`로 최초 접수일을 알아내 그 날짜로 조회한다. 못 알아내면 사실을 만들지 않는다(NULL).
    """
    problems: list[str] = []
    anchors: dict[str, str] = {}  # 접수번호 → 체인의 최초 접수일(YYYY-MM-DD)
    for r in rows:
        if r["rcept_no"] not in pending or not item_for(r["report_nm"]):
            continue
        corrected, _ = split_title(r["report_nm"])
        if not corrected:
            anchors[r["rcept_no"]] = f"{r['rcept_dt'][:4]}-{r['rcept_dt'][4:6]}-{r['rcept_dt'][6:]}"
            continue
        try:
            anchor = parse_original_date((await client.fetch_document(r["rcept_no"])).text)  # 원문은 읽기만 하고 저장하지 않는다
        except DartBadRequest as e:  # 이 정정의 본문을 받을 수 없다: 영구 오류라 사실 없이 넘어간다 (일시 오류는 올라가 다음 실행이 다시 시도)
            problems.append(f"{r['rcept_no']}: 정정 본문을 받지 못함 ({e.status or 'HTTP'})")
            continue
        if anchor is None:
            problems.append(f"{r['rcept_no']}: 정정 본문에서 최초제출일을 읽지 못함")
        else:
            anchors[r["rcept_no"]] = anchor
    payloads: dict[str, dict] = {}
    by_group: dict[tuple[str, str], list[str]] = {}
    for rcept, anchor in anchors.items():
        by_group.setdefault((item_for(next(x["report_nm"] for x in rows if x["rcept_no"] == rcept))[1], anchor), []).append(rcept)
    for (api, anchor), rcepts in by_group.items():
        api_rows: dict[str, list[dict]] = {}
        for x in await client.major_report(api, target.corp_code, _compact(anchor), _compact(anchor)):
            api_rows.setdefault(x.get("rcept_no"), []).append(x)
        for rcept in rcepts:
            if rcept in api_rows:
                matched = api_rows[rcept]
                if len(matched) == 1:
                    payloads[rcept] = build_payload(api, matched[0], rcept_no=rcept, corp_code=target.corp_code, fetched_at=fetched_at)
                else:  # 같은 접수번호의 행이 여럿이면 첫 행을 고르지 않는다 (collector.md 4-5)
                    payloads[rcept] = build_ambiguous_payload(api, len(matched), rcept_no=rcept, corp_code=target.corp_code, fetched_at=fetched_at)
                    problems.append(f"{rcept}: 항목 API({api})가 같은 접수번호에 행 {len(matched)}개를 줘 사실을 NEEDS_CHECK로 두었다")
            elif api_rows:  # 같은 체인의 더 나중 정정본이 최신이라 이 공시의 행은 없다 (정상: 그쪽에 사실이 붙는다)
                pass
            else:
                problems.append(f"{rcept}: 항목 API({api})가 최초 접수일 {anchor}에 행을 주지 않았다")
    links: dict[str, str] = {}
    for rcept, anchor in anchors.items():
        row = next(x for x in rows if x["rcept_no"] == rcept)
        corrected, base = split_title(row["report_nm"])
        if corrected:  # 정정이면 같은 제목의 최초 공시를 후보로 남긴다 (확정은 R4의 confirm_event_link)
            originals = [x["rcept_no"] for x in rows if split_title(x["report_nm"]) == (False, base) and x["rcept_dt"] == _compact(anchor)]
            if len(originals) == 1:
                links[rcept] = originals[0]
    return payloads, links, problems


def _hand_off(session, pending: set[str], payloads: dict, links: dict) -> tuple[int, int, list[str]]:
    """사실을 저장하고 인계한다. 한 공시의 사실 저장이 검증에 실패해도 그 공시만 사실 없이 넘어간다."""
    stored, problems = 0, []
    for rcept, payload in sorted(payloads.items()):
        try:
            with session.begin_nested():
                store_facts(session, rcept, payload)
            stored += 1
        except ValueError as e:  # pydantic ValidationError 포함: 사실을 만드는 코드의 버그이므로 숨기지 않고 남긴다
            problems.append(f"{rcept}: 구조화 사실 검증 실패 — {str(e).splitlines()[0][:120]}")
    for correction, original in links.items():
        row = session.get(Disclosure, correction)
        if row is not None and row.original_rcept_no is None and row.event_id is None:
            row.original_rcept_no = original
    for rcept in sorted(pending):
        mark_ready(session, rcept)
    return stored, len(pending), problems


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
        # 구조화 사실과 분석 인계: 아직 인계되지 않은 공시만 (실패한 것은 다음 실행이 다시, 인계된 것은 건드리지 않는다)
        listed = [r["rcept_no"] for r in rows]
        with get_session() as s:
            pending = set(s.scalars(select(Disclosure.rcept_no).where(Disclosure.rcept_no.in_(listed), Disclosure.analysis_ready_at.is_(None)))) if listed else set()
        payloads, links, problems = await _resolve_facts(client, target, rows, pending, now.isoformat())
        with get_session() as s:
            stored, handed, store_problems = _hand_off(s, pending, payloads, links)
        return StockOutcome(target.stock_code, True, len(new), saved, facts=stored, handed_off=handed, problems=[*problems, *store_problems])
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
