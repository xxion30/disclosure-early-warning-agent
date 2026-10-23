"""jobs 테스트: 가짜 DART + 임시 SQLite + R4의 실제 예약·실행 함수(shared/collection.py)로 종목 → 실행 → 커서까지 시험한다."""
import asyncio
from datetime import date

import pytest
from sqlalchemy import select

from collector import jobs
from collector.dart_client import DartClient
from collector.jobs import collect_once, collect_stock, create_run, dispatch_collection, run_collection
from collector.watchlist import Target
from shared.db import get_session
from shared.models import PB, CollectCursor, CollectRun, Company, Customer, Disclosure, FinancialSnapshot, Holding
from tests.collector.conftest import AUDIT_ROWS, KY, ROW_ANNUAL, ROW_NOTICE, corp_zip, fs_rows

TODAY = date(2025, 4, 1)
TARGET = Target(KY["stock_code"], KY["corp_code"], KY["name"])
HYNIX = {"corp_code": "00164779", "stock_code": "000660", "name": "SK하이닉스"}
KY_CORP = KY["corp_code"]


def run(coro):
    return asyncio.run(coro)


def fill_ky(fake):
    fake.list_rows[KY_CORP] = [ROW_NOTICE, ROW_ANNUAL]
    fake.fs[(KY_CORP, "CFS")] = fs_rows()
    fake.fs[(KY_CORP, "OFS")] = fs_rows()
    fake.audit[KY_CORP] = AUDIT_ROWS


def snapshots():
    with get_session() as s:
        return list(s.scalars(select(FinancialSnapshot).order_by(FinancialSnapshot.basis)))


def cursor(stock):
    with get_session() as s:
        return s.get(CollectCursor, stock)


def start_run(stocks):
    with get_session() as s:
        created = create_run(s, stocks)
    assert created is not None
    return created


# ---- 종목 1개 ----
def test_first_collection_backfills_twelve_months_and_saves_everything(db, fake):
    fill_ky(fake)
    out = run(collect_stock(fake.client(), TARGET, today=TODAY))
    assert out.ok and out.new_disclosures == 2 and out.snapshots == 2
    first = fake.paths("list.json")[0]
    assert (first["bgn_de"], first["end_de"]) == ("20240401", "20250401")  # 커서가 없으면 12개월 백필
    consolidated, separate = snapshots()
    assert (consolidated.basis, separate.basis) == ("CONSOLIDATED", "SEPARATE")
    assert consolidated.period == "2024" and consolidated.period_kind == "ANNUAL" and consolidated.rcept_no == "20250321001823"
    assert consolidated.indicators["AUDIT_OPINION"]["detail"] == {"opinion": "의견거절"}  # 감사의견은 연결 쪽에 붙는다
    assert "AUDIT_OPINION" not in separate.indicators
    assert consolidated.indicators["CAPITAL_IMPAIRMENT"]["detail"]["impairment_pct"] == 0.0
    assert consolidated.indicators["OPERATING_CASH_FLOW"]["detail"]["negative_years"] == 1
    assert consolidated.sources[0]["fs_div"] == "CFS" and consolidated.sources[0]["accounts"]["SHORT_TERM_DEBT"]
    with get_session() as s:
        d = s.get(Disclosure, "20250321802062")
        assert d.body_text is None and d.processed is False  # 원문은 저장하지 않는다
        assert s.get(Company, KY_CORP).name == "금양"


def test_incremental_collection_starts_a_day_before_cursor_and_skips_covered_reports(db, fake):
    fill_ky(fake)
    run(collect_stock(fake.client(), TARGET, today=TODAY))
    statement_calls = len(fake.paths("fnlttSinglAcntAll.json"))
    with get_session() as s:
        s.add(CollectCursor(stock_code="001570", last_success_at="2025-04-01T09:05:00+09:00"))
    out = run(collect_stock(fake.client(), TARGET, today=date(2025, 4, 2)))
    assert out.ok and out.new_disclosures == 0 and out.snapshots == 0  # 겹쳐 받은 공시는 새 것이 아니다
    assert fake.paths("list.json")[-1]["bgn_de"] == "20250331"
    assert len(fake.paths("fnlttSinglAcntAll.json")) == statement_calls  # 이미 있는 보고서는 다시 받지 않는다


def test_corrected_report_is_fetched_once_and_uses_latest_receipt_number(db, fake):
    """금양 FY2022: 원본 20230322001310, 정정 20230323001004·20240313001231. 재무제표 API는 최신 정정본의 값·접수번호만 준다(확인됨)."""
    base = {**ROW_ANNUAL, "report_nm": "사업보고서 (2022.12)"}
    fake.list_rows[KY_CORP] = [
        {**base, "rcept_no": "20230322001310", "rcept_dt": "20230322"},
        {**base, "rcept_no": "20230323001004", "rcept_dt": "20230323", "report_nm": "[기재정정]사업보고서 (2022.12)"},
        {**base, "rcept_no": "20240313001231", "rcept_dt": "20240313", "report_nm": "[기재정정]사업보고서 (2022.12)"},
    ]
    fake.fs[(KY_CORP, "CFS")] = fs_rows("20240313001231")
    fake.audit[KY_CORP] = AUDIT_ROWS
    out = run(collect_stock(fake.client(), TARGET, today=date(2024, 4, 1)))
    assert out.ok and out.new_disclosures == 3 and out.snapshots == 1
    assert [(s.rcept_no, s.revision) for s in snapshots()] == [("20240313001231", 1)]
    assert len(fake.paths("fnlttSinglAcntAll.json")) == 2  # 원본 처리 때 CFS·OFS 한 번씩만. 정정 두 건은 이미 충분하다고 본다


def test_server_error_becomes_failed_outcome_without_leaking_key(db, fake):
    fake.fail_corps.add(KY_CORP)
    out = run(collect_stock(fake.client(max_retries=1), TARGET, today=TODAY))
    assert not out.ok and "DartUnavailable" in out.error and "test-key" not in out.error
    with get_session() as s:
        assert list(s.scalars(select(Disclosure))) == []


# ---- 실행(run) ----
def test_run_marks_partial_and_advances_only_successful_cursor(db, fake):
    fill_ky(fake)
    fake.corp_zip = corp_zip(KY, HYNIX)
    fake.fail_corps.add(HYNIX["corp_code"])
    run_id, reserved = start_run(["001570", "000660"])
    summary = run(run_collection(run_id, reserved, client=fake.client(max_retries=1), today=TODAY))
    assert sorted(o.stock_code for o in summary.outcomes if o.ok) == ["001570"]
    with get_session() as s:
        row = s.get(CollectRun, run_id)
        assert (row.status, row.fetched_count, row.failed_count) == ("PARTIAL", 1, 1)
        assert row.error.startswith("000660:") and "test-key" not in row.error
    assert cursor("001570").last_success_at and cursor("001570").active_run_id is None
    assert cursor("000660").last_success_at is None and cursor("000660").active_run_id is None  # 실패해도 예약은 풀린다


def test_quiet_day_with_one_failure_is_partial_not_failed(db, fake):
    """새 공시가 하나도 없는 날 한 종목이 실패해도 FAILED가 아니라 PARTIAL이어야 한다 (fetched_count = 성공한 종목 수)."""
    fake.corp_zip = corp_zip(KY, HYNIX)
    fake.fail_corps.add(HYNIX["corp_code"])  # KY는 list_rows가 없어 013 (조용한 날)
    run_id, reserved = start_run(["001570", "000660"])
    run(run_collection(run_id, reserved, client=fake.client(max_retries=0), today=TODAY))
    with get_session() as s:
        assert s.get(CollectRun, run_id).status == "PARTIAL"
    assert cursor("001570").last_success_at is not None


def test_all_failed_is_failed_and_all_ok_is_done(db, fake):
    fill_ky(fake)
    run_id, reserved = start_run(["001570"])
    run(run_collection(run_id, reserved, client=fake.client(), today=TODAY))
    with get_session() as s:
        assert s.get(CollectRun, run_id).status == "DONE"
    fake.fail_corps.add(KY_CORP)
    run_id2, reserved2 = start_run(["001570"])
    run(run_collection(run_id2, reserved2, client=fake.client(max_retries=0), today=date(2025, 4, 3)))
    with get_session() as s:
        assert s.get(CollectRun, run_id2).status == "FAILED"


def test_stock_missing_from_corp_list_fails_that_stock_only(db, fake):
    fill_ky(fake)  # corpCode.xml에는 금양만 있다
    run_id, reserved = start_run(["001570", "069500"])  # 069500 = ETF
    summary = run(run_collection(run_id, reserved, client=fake.client(), today=TODAY))
    assert {o.stock_code: o.ok for o in summary.outcomes} == {"001570": True, "069500": False}
    assert cursor("069500").active_run_id is None  # 예약이 남지 않는다


def test_unknown_run_does_not_crash(db, fake):
    assert run(run_collection("no-such-run", ["001570"], client=fake.client())).outcomes == []


def test_start_failure_releases_reservations_and_fails_the_run(db, monkeypatch):
    def broken(cls, **kw):
        raise ValueError("DART_API_KEY가 비어 있습니다.")

    monkeypatch.setattr(DartClient, "from_settings", classmethod(broken))
    run_id, reserved = start_run(["001570"])
    run(run_collection(run_id, reserved))
    with get_session() as s:
        row = s.get(CollectRun, run_id)
        assert row.status == "FAILED" and "DART_API_KEY" in row.error
    assert cursor("001570").active_run_id is None  # 안 풀리면 이 종목이 계속 잠긴다


def test_create_run_refuses_while_another_global_run_is_active_or_stocks_reserved(db):
    first = start_run(["001570"])
    with get_session() as s:
        assert create_run(s, ["000660"]) is None  # 종목이 달라 예약은 가능하지만, 전체 수집이 이미 돌고 있다
    with get_session() as s:
        s.get(CollectRun, first[0]).status = "DONE"
    with get_session() as s:
        assert create_run(s, ["001570"]) is None  # 예약이 안 풀렸다 → 새 실행도 만들지 않는다
        assert len(list(s.scalars(select(CollectRun)))) == 1  # 빈 실행이 남지 않는다


# ---- 정기 수집·R4 API 연결 ----
def seed_holdings(*stocks):
    with get_session() as s:
        s.add(PB(pb_id="pb1", name="테스트PB", email="pb1@example.com", password_hash="x"))
        s.add(Customer(customer_id="c1", pb_id="pb1", name="고객", risk_profile="중립형", total_assets=100_000_000))
        s.flush()
        for stock in stocks:
            s.add(Holding(customer_id="c1", stock_code=stock, eval_amount=1_000_000, on_credit=False))


def test_collect_once_reads_holdings_excludes_etf_and_creates_companies_first(db, fake):
    fill_ky(fake)
    seed_holdings("001570", "069500")  # 069500 = KODEX 200
    summary = run(collect_once(client=fake.client()))
    assert [o.stock_code for o in summary.outcomes] == ["001570"]
    with get_session() as s:
        assert s.get(Company, KY_CORP) is not None and s.scalar(select(Company).where(Company.stock_code == "069500")) is None
        row = s.scalar(select(CollectRun))
        assert row.tenant_id is None and row.stock_codes == ["001570"] and row.status == "DONE"


def test_collect_once_does_nothing_when_a_run_is_already_active(db, fake):
    fill_ky(fake)
    seed_holdings("001570")
    start_run(["000660"])  # 다른 종목의 전체 수집이 돌고 있다
    assert run(collect_once(client=fake.client())) is None
    assert fake.paths("list.json") == []  # DART를 부르지도 않았다


def test_dispatch_returns_immediately_and_run_finishes_in_background(db, fake, monkeypatch):
    fill_ky(fake)
    monkeypatch.setattr(DartClient, "from_settings", classmethod(lambda cls, **kw: fake.client()))
    run_id, reserved = start_run(["001570"])

    async def main():
        await dispatch_collection(run_id, reserved)  # R4 API는 이것을 await하고 202를 돌려준다
        with get_session() as s:
            assert s.get(CollectRun, run_id).status != "DONE"  # 아직 안 끝났다 (백그라운드)
        await asyncio.gather(*jobs._background)

    run(main())
    with get_session() as s:
        assert s.get(CollectRun, run_id).status == "DONE"
