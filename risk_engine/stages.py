"""DARTCH rules: no DB or Agent imports; AI supplied points are never used."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from math import isfinite

from risk_engine.loader import load_rules
from risk_engine.risk_rules import RuleInputError, clean_evidence, parse_date, time_weight, minus_months

ALERT_STAGES = frozenset({'RED', 'ORANGE', 'YELLOW', 'COMPOSITE'})
# Only for the not-yet-migrated R2 runtime; stage remains authoritative.
LEGACY_LEVEL = {'RED': 'Critical', 'ORANGE': 'High', 'COMPOSITE': 'High',
                'YELLOW': 'High', 'WATCH': 'Medium', 'NONE': 'Low'}


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def judge(orange, yellow, red=False, rules=None):
    t = (rules or load_rules())['thresholds']
    ratio = orange / t['orange'] + yellow / t['yellow']
    active = []
    if red:
        active.append('RED')
    if orange >= t['orange']:
        active.append('ORANGE')
    if yellow >= t['yellow']:
        active.append('YELLOW')
    if not active and ratio >= t['composite']:
        active.append('COMPOSITE')
    if not active:
        active.append('WATCH' if orange >= t['orange'] * t['watch_ratio'] or yellow >= t['yellow'] * t['watch_ratio'] else 'NONE')
    return active[0], active, ratio


def sort_value(stage, orange, yellow, ratio, rules=None):
    t = (rules or load_rules())['thresholds']
    return {'ORANGE': orange / t['orange'], 'COMPOSITE': ratio,
            'YELLOW': yellow / t['yellow'], 'WATCH': max(orange / t['orange'], yellow / t['yellow'])}.get(stage, 0)


def priority_key(result):
    """Callers pre-sort latest_filed_at descending; stable sorting preserves ties."""
    return (load_rules()['stage_priority'][result['stage']], -result.get('sort_value', 0))


def _score(spec, detail, evidence, repeat_count):
    points = float(spec.get('base', 0))
    missing = []
    if spec.get('manual_only'):
        return 0.0, ['수치 기준 미정: 수동 확인 필요']
    if spec.get('zero_if') and detail.get(spec['zero_if']) is True and evidence:
        return 0.0, []
    for key, tiers in spec.get('tiers', {}).items():
        value = number(detail.get(key))
        if value is None:
            missing.append(key)
        else:
            points += max((add for threshold, add in tiers if value >= threshold), default=0)
    for key, choices in spec.get('values', {}).items():
        value = detail.get(key)
        if value not in choices:
            missing.append(key)
        else:
            points += choices[value]
    for key, condition in spec.get('less_than', {}).items():
        value = number(detail.get(key))
        if value is None:
            missing.append(key)
        elif value < condition['value']:
            points += condition['add']
    for key, add in spec.get('flags', {}).items():
        value = detail.get(key)
        if value is True and evidence:
            points += add
        elif value is not False:
            missing.append(key)
    repeat = spec.get('repeat')
    if repeat and repeat_count >= repeat['minimum']:
        points += repeat['add']
    return points, missing


def calculate(*, new_signals, history=(), as_of, rules=None):
    rules = rules or load_rules()
    today = parse_date(as_of)
    incoming = []
    manual = []
    categories = {st: (group, spec) for group in ('red', 'orange', 'yellow') for st, spec in rules[group].items()}
    # Merge by event/type, never merely company/type: two independent financings are two events.
    versions = {}
    for index, raw in enumerate([*history, *new_signals]):
        st = raw.get('signal_type')
        if st not in categories:
            if index >= len(history):
                raise RuleInputError(f'알 수 없는 signal_type: {st}')
            continue  # old, Phase 2 signals do not participate
        d = dict(raw.get('detail') or {})
        filed = parse_date(raw.get('filed_at') or as_of)
        if filed > today:
            raise RuleInputError('기준일 이후의 신호는 평가할 수 없습니다.')
        event = raw.get('event_id') or d.get('event_id') or raw.get('rcept_no')
        if not event:
            raise RuleInputError('event_id 또는 rcept_no가 필요합니다.')
        if d.get('is_correction') and not (raw.get('event_id') or d.get('event_id')):
            manual.append({'signal_type': st, 'reason': '정정 연결 확인 필요'})
            continue
        evidence = clean_evidence(raw.get('evidence') or d.get('evidence'))
        sig = {**raw, 'event_id': str(event), 'filed_at': filed.isoformat(), 'detail': d, 'evidence': evidence}
        key = (str(event), st)
        old = versions.get(key)
        started = parse_date(raw.get('event_started_at') or d.get('event_started_at') or (old or {}).get('event_started_at') or filed.isoformat())
        if started > filed:
            raise RuleInputError('최초 발생일이 공시일보다 늦습니다.')
        if old:
            started = min(started, parse_date(old['event_started_at']))
        sig['event_started_at'] = started.isoformat()
        if old is None or (sig['filed_at'], sig.get('rcept_no') or '') >= (old['filed_at'], old.get('rcept_no') or ''):
            versions[key] = sig
        if index >= len(history):
            incoming.append(sig)
    # Financial indicators use the latest report per type, never summed across reports.
    latest_yellow = {}
    for sig in versions.values():
        if sig['signal_type'] in rules['yellow']:
            st = sig['signal_type']
            if st not in latest_yellow or sig['filed_at'] >= latest_yellow[st]['filed_at']:
                latest_yellow[st] = sig
    rows = []
    orange = yellow = 0.0
    red = False
    for sig in versions.values():
        st, detail = sig['signal_type'], sig['detail']
        group, spec = categories[st]
        if group == 'yellow' and latest_yellow[st] is not sig:
            continue
        title = ''.join(str(detail.get('title', '')).split())
        excluded = any(marker in title for marker in ('(종속회사의주요경영사항)', '(자회사의주요경영사항)'))
        policy_pending = spec.get('enabled') is False
        excluded |= policy_pending
        excluded |= any(word in f"{detail.get('title', '')} {detail.get('reason', '')}" for word in spec.get('exclude_words', []))
        resolved = detail.get('resolved') is True or detail.get('lifted') is True
        if resolved and not sig['evidence']:
            manual.append({'signal_type': st, 'reason': '해소 근거 없음'})
            resolved = False
        weight = time_weight(parse_date(sig['event_started_at']), today, rules) if group == 'orange' else 1.0
        origin = parse_date(sig['event_started_at'])
        repeat_count = sum(1 for other in versions.values() if other['signal_type'] == st
                           and minus_months(origin, rules['history_months']) <= parse_date(other['event_started_at'])
                           and (other['event_started_at'], other['event_id']) <= (sig['event_started_at'], sig['event_id'])
                           and not other['detail'].get('resolved') and not other['detail'].get('lifted'))
        if excluded or resolved:
            raw_points, missing = 0.0, []
        elif group == 'red':
            raw_points, missing = 0.0, []
            red = True
        else:
            raw_points, missing = _score(spec, detail, sig['evidence'], repeat_count)
        if missing and weight:
            manual.append({'signal_type': st, 'missing': missing})
        points = raw_points * weight
        if group == 'orange':
            orange += points
        if group == 'yellow':
            yellow += points
        sig['points'] = raw_points
        sig['detail'] = {**detail, 'event_id': sig['event_id'], 'event_started_at': sig['event_started_at'], 'evidence': sig['evidence']}
        rows.append({'group': group.upper(), 'item': spec['label'], 'signal_type': st, 'event_id': sig['event_id'],
                     'rcept_no': sig.get('rcept_no'), 'filed_at': sig['filed_at'], 'points': points,
                     'raw_points': raw_points, 'weight': weight, 'active': not excluded and not resolved and bool(weight) and (group == 'red' or points > 0),
                     'excluded': excluded, 'policy_pending': policy_pending, 'resolved': resolved, 'quote': detail.get('quote'), 'evidence': sig['evidence'], 'missing': missing})
        if st == 'CAPITAL_IMPAIRMENT' and 'basis_checks' in detail:
            rows[-1].update(fs_div=detail.get('fs_div'), basis_checks=detail['basis_checks'])
    incoming_ids = {id(item) for item in incoming}
    incoming = [sig for sig in versions.values() if id(sig) in incoming_ids]
    stage, active, ratio = judge(orange, yellow, red, rules)
    digest = hashlib.sha256(json.dumps(rules, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {'as_of': today.isoformat(), 'rules_digest': digest, 'stage': stage, 'active_stages': active, 'orange_score': orange, 'yellow_score': yellow,
            'composite_ratio': ratio, 'sort_value': sort_value(stage, orange, yellow, ratio, rules),
            'manual_check': bool(manual), 'manual_reasons': manual, 'breakdown': rows, 'signals': incoming,
            'level': LEGACY_LEVEL[stage], 'total_score': orange + yellow, 'combo_upgraded': stage == 'COMPOSITE'}
