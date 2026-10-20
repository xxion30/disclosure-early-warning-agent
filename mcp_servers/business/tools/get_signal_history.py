from datetime import date

from sqlalchemy import select

from mcp_servers.business.rules.risk_rules import minus_months, parse_date
from mcp_servers.business.tools._common import guarded, invalid, require_company
from shared.db import get_session
from shared.models import Signal
from shared.schemas import ok


def history_rows(s, corp_code: str, months: int, as_of: date) -> list[dict]:
    start = minus_months(as_of, months).isoformat()
    rows = s.scalars(
        select(Signal)
        .where(Signal.corp_code == corp_code, Signal.filed_at >= start, Signal.filed_at <= f"{as_of.isoformat()}~")
        .order_by(Signal.filed_at, Signal.signal_id)
    ).all()
    return [
        {
            "signal_type": r.signal_type,
            "points": r.points,
            "detail": r.detail or {},
            "rcept_no": r.rcept_no,
            "filed_at": r.filed_at,
        }
        for r in rows
    ]


@guarded
def get_signal_history(corp_code: str, months: int = 12, as_of: str | None = None) -> dict:
    """회사의 과거 위험 신호 목록 (최근 months개월, as_of 이전만).

    Args:
        corp_code: DART 고유번호
        months: 거슬러 올라갈 개월 수 (1~36, 기본 12)
        as_of: 기준일 YYYY-MM-DD. 비우면 오늘. 과거 공시를 재생할 때는 그 공시의 접수일을 넣는다.
    """
    if not isinstance(months, int) or isinstance(months, bool) or not 1 <= months <= 36:
        raise invalid("months는 1~36 사이의 정수여야 합니다.")
    as_of_d = parse_date(as_of) if as_of else date.today()
    with get_session() as s:
        require_company(s, corp_code)
        rows = history_rows(s, corp_code, months, as_of_d)
        return ok({"corp_code": corp_code, "months": months, "as_of": as_of_d.isoformat(), "signals": rows})
