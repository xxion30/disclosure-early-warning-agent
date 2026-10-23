from datetime import date

from sqlalchemy import select

from risk_engine import risk_rules
from risk_engine.stages import calculate
from risk_engine.loader import load_rules
from mcp_servers.business.tools._common import guarded, invalid, require_company
from mcp_servers.business.tools.get_signal_history import history_rows
from shared.db import get_session
from shared.models import Disclosure, FinancialSnapshot, RiskState
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
        current_state = s.get(RiskState, corp_code)
        expected_version = current_state.state_version if current_state else 0
        # 시장 메타데이터는 DB에서 보강한다. 횡령·배임은 금액 기준 없이 RED다.
        new_signals = [
            {**sig, "detail": {"market": company.market, **(sig.get("detail") or {})}} for sig in new_signals
        ]
        # 정정 공시 여부는 공시 제목으로 코드가 정한다 (rules.yaml corrections). AI가 넘긴 값은 쓰지 않는다.
        prefixes = tuple((rules.get("corrections") or {}).get("title_prefixes", []))
        rcepts = [sig.get("rcept_no") for sig in new_signals if sig.get("rcept_no")]
        if prefixes and rcepts:
            titles = dict(s.execute(select(Disclosure.rcept_no, Disclosure.title).where(Disclosure.rcept_no.in_(rcepts))).all())
            for sig in new_signals:
                title = titles.get(sig.get("rcept_no")) or ""
                sig["detail"] = {**sig["detail"], "is_correction": title.lstrip().startswith(prefixes), "title": title}
        history = history_rows(s, corp_code, rules["history_months"], as_of_d)
        # DB metadata establishes event links; model-supplied event IDs are not authoritative.
        for sig in new_signals:
            disc = s.get(Disclosure, sig.get('rcept_no')) if sig.get('rcept_no') else None
            if disc is None or disc.corp_code != corp_code:
                raise invalid('신호의 접수번호와 회사가 일치하는 공시가 필요합니다.')
            sig['detail']['title'] = disc.title
            sig['filed_at'] = disc.filed_at
            sig.pop('event_id', None)
            sig.pop('event_started_at', None)
            sig['detail'].pop('event_id', None)
            sig['detail'].pop('event_started_at', None)
            # R3 original_rcept_no is a candidate; only an R4-confirmed event_id authorizes merging.
            root = s.get(Disclosure, disc.original_rcept_no) if disc.original_rcept_no and disc.event_id else None
            if root and root.corp_code != corp_code:
                raise invalid('원공시의 회사가 일치하지 않습니다.')
            if disc.event_id or root:
                sig['event_id'] = disc.event_id or root.event_id or root.rcept_no
                sig['event_started_at'] = disc.event_started_at or (root.event_started_at or root.filed_at if root else disc.filed_at)
        # Read R3-produced snapshots through DB only. Do not import the collector.
        snapshots = s.scalars(select(FinancialSnapshot).join(Disclosure, Disclosure.rcept_no == FinancialSnapshot.rcept_no)
                              .where(FinancialSnapshot.corp_code == corp_code, Disclosure.corp_code == corp_code,
                                     Disclosure.filed_at <= as_of_d.isoformat() + '~')).all()
        from risk_engine.financials import select_snapshot_signals
        financial_types={'CAPITAL_IMPAIRMENT','OPERATING_CASH_FLOW','INTEREST_BURDEN','SHORT_TERM_DEBT'}
        # Numerical indicators come from R3 snapshots, never from model/caller overrides.
        requested={sig['signal_type']:sig for sig in [*history,*new_signals] if sig['signal_type'] in financial_types}
        financial=select_snapshot_signals(snapshots,{d.rcept_no:d.filed_at for d in s.scalars(select(Disclosure).where(Disclosure.corp_code==corp_code))}) if snapshots else []
        selected={sig['signal_type'] for sig in financial}
        for st,previous in requested.items():
            if st not in selected:
                financial.append({'signal_type':st,'event_id':f'financial:{corp_code}:{st}',
                    'rcept_no':previous.get('rcept_no'),'filed_at':previous.get('filed_at') or as_of_d.isoformat(),
                    'detail':{},'evidence':[]})
        explicit={sig['signal_type'] for sig in new_signals if sig['signal_type'] not in financial_types}
        history=[sig for sig in history if sig['signal_type'] not in financial_types and sig['signal_type'] not in (selected-explicit)]
        history.extend(sig for sig in financial if sig['signal_type'] not in explicit)
        new_signals=[sig for sig in new_signals if sig['signal_type'] not in financial_types]
        result = calculate(history=history, new_signals=new_signals, as_of=as_of_d.isoformat(), rules=rules)
        # Verify API references against their own receipt, including history.
        from shared.facts import verify_api_reference
        for row in result['breakdown']:
            for evidence in row['evidence']:
                if evidence.get('kind') == 'api':
                    disc = s.get(Disclosure, evidence['rcept_no'])
                    if (disc is None or disc.corp_code != corp_code
                            or disc.filed_at[:10] > as_of_d.isoformat()):
                        raise invalid('API 근거의 기업·공시일이 판정 입력과 다릅니다.')
                    verify_api_reference(evidence, disc.structured_facts)
    result["expected_state_version"] = expected_version
    result["corp_code"] = corp_code
    result["as_of"] = as_of_d.isoformat()
    return ok(result)
