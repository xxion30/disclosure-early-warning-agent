"""정기보고서 제목 해석: list.json의 report_nm → 재무제표 API 호출에 필요한 값.

관측한 제목(삼성전자·금양·위니아): `사업보고서 (2024.12)`, `반기보고서 (2024.06)`, `분기보고서 (2024.03)`, `분기보고서 (2024.09)`,
정정은 `[기재정정]` 접두. 12월 결산이 아닌 회사는 월이 달라 Phase 1에서는 건너뛴다(None) — 몇 곳인지는 확인하지 못했다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PERIODIC = re.compile(r"^(?P<corr>\[[^\]]*정정\])?\s*(?P<kind>사업|반기|분기)보고서\s*\((?P<year>\d{4})\.(?P<month>\d{2})\)\s*$")

# (보고서 종류, 결산월) → (reprt_code, 분기 번호). 사업보고서는 분기 번호 없음.
_CODES = {("사업", 12): ("11011", None), ("분기", 3): ("11013", 1), ("반기", 6): ("11012", 2), ("분기", 9): ("11014", 3)}


@dataclass(frozen=True)
class PeriodicReport:
    bsns_year: int
    reprt_code: str  # 11011 사업 / 11012 반기 / 11013 1분기 / 11014 3분기
    period: str  # financial_snapshots.period: "2024" 또는 "2025-Q1"
    period_kind: str  # ANNUAL / YTD (분기·반기는 R4 fixture와 같은 YTD. 재무상태표 기반 지표만 만들어 구분 영향이 없다)
    is_correction: bool


def parse_periodic_title(title: str) -> PeriodicReport | None:
    m = PERIODIC.match((title or "").strip())
    if not m:
        return None
    year, month = int(m["year"]), int(m["month"])
    hit = _CODES.get((m["kind"], month))
    if hit is None:
        return None
    code, quarter = hit
    if quarter is None:
        return PeriodicReport(year, code, str(year), "ANNUAL", bool(m["corr"]))
    return PeriodicReport(year, code, f"{year}-Q{quarter}", "YTD", bool(m["corr"]))
