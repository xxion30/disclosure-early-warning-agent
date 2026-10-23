"""수집 결과를 DB 표에 넣는다. 모든 함수는 세션을 받고, 커밋은 부르는 쪽(`get_session()`)이 한다. 같은 입력을 다시 넣어도 중복되지 않는다.

- `disclosures`: 본문(`body_text`)은 **항상 비워 둔다** (원문은 저장하지 않는다). 새 공시는 `processed=False`로 들어간다.
- `financial_snapshots`: 같은 (corp_code, period, basis)에 접수번호가 여러 개면(정정) 접수번호 순서대로 revision 1, 2, …을 매긴다.
  DART 재무제표 API는 정정본 중 **최신 것의 값과 접수번호**만 돌려주므로(확인됨), 수집하는 동안 새 접수번호가 나타날 때마다 revision이 쌓인다.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from collector.reports import PeriodicReport
from collector.watchlist import Target
from shared.models import Company, Disclosure, FinancialSnapshot

DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_date(rcept_dt: str) -> str:
    """'20250321' → '2025-03-21'."""
    return f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}"


def upsert_company(session: Session, target: Target) -> Company:
    """수집 대상 회사를 `companies`에 넣는다 (공시의 corp_code 외래키가 이 표를 가리킨다). 점수 관련 칸은 건드리지 않는다."""
    company = session.get(Company, target.corp_code)
    if company is None:
        company = Company(corp_code=target.corp_code, stock_code=target.stock_code, name=target.name)
        session.add(company)
    else:
        company.stock_code, company.name = target.stock_code, target.name
    session.flush()
    return company


def upsert_disclosures(session: Session, corp_code: str, rows: list[dict]) -> list[str]:
    """list.json 행을 `disclosures`에 넣고 **새로 들어간** 접수번호를 돌려준다. 이미 있는 공시는 바꾸지 않는다."""
    wanted = {r["rcept_no"]: r for r in rows}
    if not wanted:
        return []
    existing = set(session.scalars(select(Disclosure.rcept_no).where(Disclosure.rcept_no.in_(wanted))))
    new = []
    for rcept_no in sorted(set(wanted) - existing):
        r = wanted[rcept_no]
        session.add(Disclosure(
            rcept_no=rcept_no, corp_code=corp_code, title=(r.get("report_nm") or "").strip(),
            filed_at=_iso_date(r["rcept_dt"]), url=DART_VIEW_URL.format(rcept_no=rcept_no), body_text=None, processed=False,
        ))
        new.append(rcept_no)
    session.flush()
    return new


def save_snapshot(
    session: Session, corp_code: str, report: PeriodicReport, basis: str, rcept_no: str, indicators: dict, sources: list[dict]
) -> bool:
    """재무 지표 스냅샷 저장. 새 행이면 True. 같은 접수번호가 이미 있으면 지표·출처만 갱신하고 False."""
    same = list(session.scalars(select(FinancialSnapshot).where(
        FinancialSnapshot.corp_code == corp_code, FinancialSnapshot.period == report.period, FinancialSnapshot.basis == basis)))
    current = next((s for s in same if s.rcept_no == rcept_no), None)
    if current is not None:
        current.indicators, current.sources = indicators, sources
        session.flush()
        return False
    session.add(FinancialSnapshot(corp_code=corp_code, rcept_no=rcept_no, period=report.period, period_kind=report.period_kind,
                                  basis=basis, indicators=indicators, sources=sources, revision=len(same) + 1, created_at=_now()))
    session.flush()
    ordered = session.scalars(select(FinancialSnapshot).where(
        FinancialSnapshot.corp_code == corp_code, FinancialSnapshot.period == report.period, FinancialSnapshot.basis == basis,
    ).order_by(FinancialSnapshot.rcept_no))
    for revision, snap in enumerate(ordered, start=1):  # 접수번호는 시간순이라 늦게 들어온 옛 번호도 제자리에 들어간다
        snap.revision = revision
    session.flush()
    return True
