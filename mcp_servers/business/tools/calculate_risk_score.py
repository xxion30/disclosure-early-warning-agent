from datetime import date

from mcp_servers.business.rules import risk_rules
from mcp_servers.business.rules.loader import load_rules
from mcp_servers.business.tools._common import guarded, invalid, require_company
from mcp_servers.business.tools.get_signal_history import history_rows
from shared.db import get_session
from shared.schemas import ok


@guarded
def calculate_risk_score(corp_code: str, new_signals: list[dict], as_of: str) -> dict:
    """규칙표(rules.yaml)로 위험 점수와 등급을 계산한다. 점수는 이 도구만 정하고 AI가 바꿀 수 없다.

    Args:
        corp_code: DART 고유번호
        new_signals: 이번 공시에서 찾은 신호 목록. 각 항목은
            {"signal_type": str, "detail": {원문에서 찾은 값, "quote": 원문 인용}, "filed_at": 접수일, "rcept_no": 접수번호}.
            points는 넣어도 무시한다. 신호가 없으면 빈 목록.
        as_of: 기준일 YYYY-MM-DD (보통 이번 공시 접수일)

    Returns:
        total_score, level, combo_upgraded, breakdown(점수 내역, 항목마다 quote), signals(저장용으로 정리된 새 신호)
    """
    if not isinstance(new_signals, list) or not all(isinstance(x, dict) for x in new_signals):
        raise invalid("new_signals는 객체 목록이어야 합니다.")
    as_of_d: date = risk_rules.parse_date(as_of)
    rules = load_rules()
    with get_session() as s:
        company = require_company(s, corp_code)
        # 시장(KOSPI/KOSDAQ)은 공시가 아니라 회사 정보에서 온다 (횡령·배임 기준 5% / 3% 구분).
        new_signals = [
            {**sig, "detail": {"market": company.market, **(sig.get("detail") or {})}} for sig in new_signals
        ]
        history = history_rows(s, corp_code, rules["history_months"], as_of_d)
        result = risk_rules.calculate(
            base_score=company.base_score or 0,
            base_reasons=company.base_reasons or [],
            history=history,
            new_signals=new_signals,
            as_of=as_of_d.isoformat(),
            rules=rules,
        )
    result["corp_code"] = corp_code
    result["as_of"] = as_of_d.isoformat()
    return ok(result)
