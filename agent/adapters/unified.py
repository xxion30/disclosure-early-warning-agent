"""R4-owned mappings for the R2 hosted graph; never infer numbers with the model."""
from agent.integration import UnifiedBindings
from agent.ports import AgentError
from agent.adapters.phase1 import make_phase1_classifier
from agent.adapters.budgets import record_budget
from shared.rule_catalog import load_catalog
from shared.title_policy import title_disposition


def approved_routes(title):
    catalog = load_catalog()
    kind = make_phase1_classifier()(title)
    routes = []
    for item in catalog['allowed'].get(kind, []):
        if item in ('CAPITAL_IMPAIRMENT', 'OPERATING_CASH_FLOW', 'INTEREST_BURDEN', 'SHORT_TERM_DEBT'):
            continue
        bucket = catalog['buckets'][item]
        # Financial numbers are supplied by R3 snapshots in calculate_risk_score.
        flags = catalog['text_flags'][item]
        if bucket == 'RED':
            flags = ['reason']
        routes.append({'bucket': bucket, 'item': item, 'text_flags': flags})
    return routes


# (fact name, expected unit). No thresholds or scores belong in this adapter.
FACT_FIELDS = {
    'RIGHTS_OFFERING': {'ratio_pct': ('issue_ratio_common_pct', '%'),
                        'third_party': ('third_party_allocation', None)},
    'CAPITAL_REDUCTION': {'reduction_ratio_pct': ('reduction_ratio_common_pct', '%')},
}


def prepare_signals(payload):
    disclosure = payload['disclosure']
    if payload['routes'] != approved_routes(disclosure['title']):
        raise AgentError('승인된 제목 분류와 계산 입력 경로 불일치')
    catalog = load_catalog()
    facts = payload['structured_facts'].get('facts', [])
    flags = payload.get('flags', [])
    signals = []
    for route in payload['routes']:
        item = route['item']
        if title_disposition(disclosure['title'], item) == 'needs_check':
            raise AgentError('제목의 신규·후속 구분에 R1 확인 필요')
        detail, evidence = {}, []
        for flag in flags:
            if flag['item'] != item:
                continue
            if flag['flag'] not in route['text_flags'] or flag['value'] is None:
                raise AgentError('승인되지 않거나 확인되지 않은 텍스트 조건')
            detail[flag['flag']] = flag['value']
            evidence.extend(flag['evidence'])
        for key, (name, unit) in FACT_FIELDS.get(item, {}).items():
            candidates = [fact for fact in facts if fact['name'] == name]
            if not candidates or (len(candidates) == 1 and candidates[0]['status'] != 'PRESENT'):
                continue  # validated quoted facts may supply the field below
            if len(candidates) != 1:
                raise AgentError(f'API 다중 행 구분 필요: {name}')
            fact = candidates[0]
            if fact.get('unit') != unit:
                raise AgentError(f'API 사실 단위 불일치: {name}')
            if key in detail and detail[key] != fact['value']:
                raise AgentError(f'API 사실과 텍스트 조건 충돌: {name}')
            detail[key] = fact['value']
            evidence.append({'kind': 'api', **{
                k: fact['source'].get(k, fact.get(k, payload['structured_facts'].get(k)))
                for k in ('api', 'field', 'rcept_no', 'row_key', 'value', 'unit', 'period', 'basis', 'data_version')}})
        for fact in payload.get('numeric_facts', []):
            if fact['item'] != item:
                continue
            key = fact['field']
            if key not in catalog['numeric_fields'][item] or fact['unit'] != '%':
                raise AgentError('허용되지 않은 수치 사실')
            if key in detail and detail[key] != fact['value']:
                raise AgentError('API와 원문 수치가 달라 확인 필요')
            detail[key] = fact['value']
            evidence.extend(fact['evidence'])
        if item == 'CAPITAL_REDUCTION':
            other = [f for f in facts if f['name'] == 'reduction_ratio_other_pct' and f['status'] == 'PRESENT']
            if any(f['value'] != detail.get('reduction_ratio_pct') for f in other):
                raise AgentError('종류주식과 보통주 감자 비율이 달라 확인 필요')
        required = set(catalog['numeric_fields'][item])
        if required - set(detail):
            raise AgentError('계산에 필요한 확정 수치가 없습니다: ' + ', '.join(sorted(required - set(detail))))
        if not evidence:
            raise AgentError('위험 항목의 검증된 근거가 없습니다')
        signals.append({'signal_type': item, 'rcept_no': disclosure['rcept_no'],
                        'detail': detail, 'evidence': evidence})
    return signals


async def ensure_current(payload):
    """Compatibility review port; hosted approval remains in the R4 transaction."""
    from sqlalchemy import select
    from shared.db import get_session
    from shared.models import PublicAssessment, OrganizationReview, RiskState, Customer
    context = payload['context']
    if context.get('kind') != 'review' or not context.get('tenant_id'):
        return False
    with get_session() as db:
        assessment = db.get(PublicAssessment, payload.get('assessment_id'))
        if assessment is None:
            return False
        state = db.get(RiskState, assessment.corp_code)
        review = db.scalar(select(OrganizationReview).where(
            OrganizationReview.tenant_id == context['tenant_id'],
            OrganizationReview.assessment_id == assessment.assessment_id))
        ids = payload.get('customer_ids', [])
        owned = set(db.scalars(select(Customer.customer_id).where(
            Customer.tenant_id == context['tenant_id'], Customer.customer_id.in_(ids))))
        return bool(review and state and assessment.state_version > 0 and state.state_version == assessment.state_version
                    and not state.resolved
                    and state.stage in ('RED', 'ORANGE', 'YELLOW', 'COMPOSITE')
                    and review.status in ('PENDING', 'RUNNING', 'WAITING_APPROVAL', 'MANUAL_CHECK')
                    and assessment.rcept_no == payload['rcept_no']
                    and assessment.job_id == context['assessment_key']
                    and assessment.input_hash == context['input_version']
                    and assessment.rules_digest == context['rules_version']
                    and assessment.as_of == context['as_of'] and owned == set(ids))


def bindings():
    from risk_engine.stages import financial_missing_only
    return UnifiedBindings(approved_routes, prepare_signals, ensure_current, record_budget,
                           can_publish_partial=financial_missing_only)
