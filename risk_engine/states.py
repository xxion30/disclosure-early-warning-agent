"""Versioned current-state storage. Public analysis is independent of approval status."""
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from shared.models import Company, RiskState
from risk_engine.loader import load_rules
from risk_engine.risk_rules import clean_evidence, parse_date
from risk_engine.stages import ALERT_STAGES, priority_key, number


class StaleStateError(ValueError):
    pass


def score_fingerprint(result):
    # Ignore compare-and-swap token and transport-only source chunks.
    fields = ('stage', 'active_stages', 'orange_score', 'yellow_score', 'composite_ratio',
              'sort_value', 'manual_check', 'as_of', 'rules_digest')
    data = {k: result.get(k) for k in fields}
    data['items'] = [{k: v for k, v in row.items() if k not in ('source_chunks', 'from_history')}
                     for row in result.get('breakdown', [])]
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def update_state(session, corp_code, result, filed_at, *, resolution_evidence=None, now=None):
    from shared.models.risk import STAGES
    if result.get('stage') not in STAGES:
        raise ValueError('유효한 stage가 필요합니다.')
    for key in ('orange_score', 'yellow_score', 'composite_ratio', 'sort_value'):
        value = number(result.get(key, 0))
        if value is None or value < 0:
            raise ValueError(f'{key}는 유한한 음수 아닌 수여야 합니다.')
    company = session.scalar(select(Company).where(Company.corp_code == corp_code).with_for_update())
    if company is None:
        raise ValueError('회사를 찾을 수 없습니다.')
    row = session.scalar(select(RiskState).where(RiskState.corp_code == corp_code).with_for_update().execution_options(populate_existing=True))
    as_of = parse_date(result.get('as_of') or filed_at).isoformat()
    if row and (filed_at < row.latest_filed_at or as_of < (row.as_of or row.latest_filed_at)):
        raise StaleStateError('이전 기준일의 판정은 최신 위험 상태를 덮을 수 없습니다.')
    expected = result.get('expected_state_version')
    version = row.state_version if row else 0
    if expected is not None and expected != version:
        raise StaleStateError('위험 상태가 변경됐습니다. 최신 데이터로 재계산해야 합니다.')
    old_types = {i['signal_type'] for i in (row.items if row else []) if i.get('active')}
    new_types = {i['signal_type'] for i in result.get('breakdown', []) if i.get('active')}
    if resolution_evidence is None and result['stage'] == 'NONE':
        relevant = [i for i in result.get('breakdown', []) if not i.get('excluded')]
        # Benign zero-point observations do not prevent confirmed resolution.
        risks = [i for i in relevant if i.get('resolved') or i.get('active') or i.get('raw_points', 0) > 0]
        if risks and all(i.get('resolved') for i in risks):
            resolution_evidence = [e for i in risks for e in i.get('evidence', [])]
    resolution = clean_evidence(resolution_evidence)
    if resolution_evidence and not resolution:
        raise ValueError('해소 근거 형식이 올바르지 않습니다.')
    if resolution and result['stage'] != 'NONE':
        raise ValueError('활성 위험이 있는 종목 전체를 해소 처리할 수 없습니다.')
    old = {'stage': row.stage, 'sort_value': row.sort_value} if row else {'stage': 'NONE', 'sort_value': 0}
    old_red = {(i.get('event_id'), i.get('signal_type')) for i in (row.items if row else [])
               if i.get('active') and i.get('group') == 'RED'}
    new_red = {(i.get('event_id'), i.get('signal_type')) for i in result.get('breakdown', [])
               if i.get('active') and i.get('group') == 'RED'}
    def content(stage, items, manual, resolved, evidence, digest):
        # Recalculation date alone does not invalidate otherwise identical drafts.
        return (stage, [{k: v for k, v in i.items() if k not in ('source_chunks', 'from_history')}
                        for i in items], manual, resolved, evidence, digest)
    before = (content(row.stage, row.items, row.manual_check, bool(row.resolved), row.resolution_evidence or [], row.rules_digest),
              row.orange_score, row.yellow_score, row.composite_ratio, row.sort_value, row.active_stages) if row else None
    if row is None:
        row = RiskState(corp_code=corp_code, stock_code=company.stock_code, alert_version=0, state_version=0)
        session.add(row)
    is_alert = result['stage'] in ALERT_STAGES
    raised = priority_key(result)[0] < priority_key(old)[0]
    if is_alert and (raised or new_types - old_types or new_red - old_red):
        row.alert_version += 1
    row.stage = result['stage']
    for key in ('orange_score', 'yellow_score', 'composite_ratio', 'sort_value'):
        setattr(row, key, result.get(key, 0))
    row.active_stages = result.get('active_stages', [row.stage])
    # Preserve validated citations, but never put fetched raw RAG chunks in the public-state DB.
    row.items = [{k: v for k, v in i.items() if k != 'source_chunks'} for i in result.get('breakdown', [])]
    row.manual_check = result.get('manual_check', False)
    if resolution:
        row.resolved, row.resolution_evidence = True, resolution
    elif row.stage != 'NONE':
        row.resolved = False
        row.resolution_evidence = []
    row.as_of = as_of
    row.rules_digest = result.get('rules_digest')
    after = (content(row.stage, row.items, row.manual_check, bool(row.resolved), row.resolution_evidence or [], row.rules_digest),
             row.orange_score, row.yellow_score, row.composite_ratio, row.sort_value, row.active_stages)
    if before != after:
        row.state_version += 1
    row.updated_at = now or datetime.now(timezone.utc).isoformat()
    row.latest_filed_at = filed_at
    session.flush()
    return row
