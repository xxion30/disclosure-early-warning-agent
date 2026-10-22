"""Transactional current-state storage and PB read versions (R4)."""
from sqlalchemy import select
from shared.models import Company, RiskState
from risk_engine.stages import ALERT_STAGES, priority_key


def update_state(session, corp_code, result, filed_at, *, resolution_evidence=None, now=None):
    from datetime import datetime, timezone
    from risk_engine.risk_rules import clean_evidence
    from shared.models.risk import STAGES
    from risk_engine.stages import number

    if result.get('stage') not in STAGES:
        raise ValueError('유효한 stage가 필요합니다.')
    for key in ('orange_score', 'yellow_score', 'composite_ratio', 'sort_value'):
        value = number(result.get(key, 0))
        if value is None or value < 0:
            raise ValueError(f'{key}는 유한한 음수 아닌 수여야 합니다.')
    company = session.scalar(select(Company).where(Company.corp_code == corp_code).with_for_update())
    if company is None:
        raise ValueError('회사를 찾을 수 없습니다.')
    row = session.scalar(select(RiskState).where(RiskState.corp_code == corp_code).with_for_update())
    if row is not None and filed_at < row.latest_filed_at:
        return row
    old = {'stage': row.stage, 'sort_value': row.sort_value} if row else {'stage': 'NONE', 'sort_value': 0}
    old_types = {i['signal_type'] for i in (row.items if row else []) if i.get('active')}
    new_types = {i['signal_type'] for i in result.get('breakdown', []) if i.get('active')}
    if resolution_evidence is None and result['stage'] == 'NONE':
        relevant = [i for i in result.get('breakdown', []) if not i.get('excluded')]
        if relevant and all(i.get('resolved') for i in relevant):
            resolution_evidence = [e for i in relevant for e in i.get('evidence', [])]
    resolution = clean_evidence(resolution_evidence)
    if resolution_evidence and not resolution:
        raise ValueError('해소 근거 형식이 올바르지 않습니다.')
    if row is None:
        row = RiskState(corp_code=corp_code, stock_code=company.stock_code, alert_version=0)
        session.add(row)
    is_alert = result['stage'] in ALERT_STAGES
    # No dedicated recurrence detection; these are only the existing basic alert triggers.
    raised = priority_key(result)[0] < priority_key(old)[0]
    changed = bool(new_types - old_types)
    old_red = {(i.get('event_id'), i.get('signal_type')) for i in (row.items or [])
               if i.get('active') and i.get('group') == 'RED'}
    new_red = {(i.get('event_id'), i.get('signal_type')) for i in result.get('breakdown', [])
               if i.get('active') and i.get('group') == 'RED'}
    new_red_event = bool(new_red - old_red)
    if is_alert and (raised or changed or new_red_event):
        row.alert_version += 1
    row.stage = result['stage']
    for key in ('orange_score', 'yellow_score', 'composite_ratio', 'sort_value'):
        setattr(row, key, result.get(key, 0))
    row.active_stages = result.get('active_stages', [row.stage])
    row.items = result.get('breakdown', [])
    row.manual_check = result.get('manual_check', False)
    if resolution and result['stage'] != 'NONE':
        raise ValueError('활성 위험이 있는 종목 전체를 해소 처리할 수 없습니다.')
    if resolution:
        row.resolved = True
        row.resolution_evidence = resolution
    elif result['stage'] != 'NONE':
        row.resolved = False
    row.updated_at = now or datetime.now(timezone.utc).isoformat()
    row.latest_filed_at = filed_at
    session.flush()
    return row
