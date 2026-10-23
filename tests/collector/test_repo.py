"""repo 테스트: 임시 SQLite(메모리)에 R4의 실제 모델로 만든다. 공시 행은 실제 금양 목록에서 본 값이다."""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from collector.repo import save_snapshot, upsert_company, upsert_disclosures
from collector.reports import PeriodicReport
from collector.watchlist import Target
from shared.models import Base, Company, Disclosure, FinancialSnapshot

KY = Target("001570", "00106119", "금양")
ROW_A = {"rcept_no": "20250321802062", "rcept_dt": "20250321", "report_nm": "투자유의안내              (상장폐지 기준 해당)"}
ROW_B = {"rcept_no": "20250410801159", "rcept_dt": "20250410", "report_nm": "기타시장안내              (상장폐지 이의신청서 제출)"}
ANNUAL_2022 = PeriodicReport(2022, "11011", "2022", "ANNUAL", False)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_upsert_company_keeps_score_fields(session):
    upsert_company(session, KY)
    company = session.get(Company, "00106119")
    company.base_score = 7  # R4가 쓰는 칸
    upsert_company(session, Target("001570", "00106119", "금양(개명)"))
    again = session.get(Company, "00106119")
    assert again.name == "금양(개명)" and again.base_score == 7
    assert len(list(session.scalars(select(Company)))) == 1


def test_upsert_disclosures_inserts_without_body_and_unprocessed(session):
    upsert_company(session, KY)
    assert upsert_disclosures(session, "00106119", [ROW_B, ROW_A]) == ["20250321802062", "20250410801159"]
    d = session.get(Disclosure, "20250321802062")
    assert d.body_text is None and d.processed is False and d.filed_at == "2025-03-21"
    assert d.url == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250321802062"
    assert d.title == "투자유의안내              (상장폐지 기준 해당)".strip()


def test_upsert_disclosures_is_idempotent_and_keeps_processed_flag(session):
    upsert_company(session, KY)
    upsert_disclosures(session, "00106119", [ROW_A])
    session.get(Disclosure, "20250321802062").processed = True  # 분석이 끝났다
    assert upsert_disclosures(session, "00106119", [ROW_A, ROW_A, ROW_B]) == ["20250410801159"]  # 새 것만, 중복 입력 무시
    assert session.get(Disclosure, "20250321802062").processed is True


def test_upsert_disclosures_empty(session):
    assert upsert_disclosures(session, "00106119", []) == []


def snap(session, rcept, indicators=None, basis="CONSOLIDATED"):
    return save_snapshot(session, "00106119", ANNUAL_2022, basis, rcept, indicators or {"CAPITAL_IMPAIRMENT": {"status": "OK"}}, [])


def revisions(session, basis="CONSOLIDATED"):
    rows = session.scalars(select(FinancialSnapshot).where(FinancialSnapshot.basis == basis).order_by(FinancialSnapshot.rcept_no))
    return [(s.rcept_no, s.revision) for s in rows]


def test_snapshot_first_save_is_revision_one_and_rerun_does_not_duplicate(session):
    assert snap(session, "20230322001310") is True
    assert snap(session, "20230322001310", {"CAPITAL_IMPAIRMENT": {"status": "MISSING"}}) is False  # 다시 돌려도 새 행이 아니다
    assert revisions(session) == [("20230322001310", 1)]
    assert session.scalars(select(FinancialSnapshot)).one().indicators["CAPITAL_IMPAIRMENT"]["status"] == "MISSING"  # 값은 갱신


def test_correction_arriving_later_becomes_next_revision(session):  # 금양 FY2022: 원본 20230322001310, 정정 20240313001231
    snap(session, "20230322001310")
    snap(session, "20240313001231")
    assert revisions(session) == [("20230322001310", 1), ("20240313001231", 2)]


def test_older_receipt_arriving_late_is_renumbered_in_order(session):
    snap(session, "20240313001231")  # 백필이 최신 정정본만 받았다
    snap(session, "20230322001310")  # 나중에 원본이 들어옴
    assert revisions(session) == [("20230322001310", 1), ("20240313001231", 2)]


def test_basis_and_period_are_separate_series(session):
    snap(session, "20230322001310", basis="CONSOLIDATED")
    snap(session, "20230322001310", basis="SEPARATE")
    save_snapshot(session, "00106119", PeriodicReport(2023, "11011", "2023", "ANNUAL", False), "CONSOLIDATED", "20240327001225", {}, [])
    assert revisions(session, "CONSOLIDATED")[0][1] == 1 and revisions(session, "SEPARATE") == [("20230322001310", 1)]
    assert {s.revision for s in session.scalars(select(FinancialSnapshot))} == {1}
