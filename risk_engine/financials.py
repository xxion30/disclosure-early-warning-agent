"""Validate financial period/value contracts; collection/formulas are R3's responsibility."""
from risk_engine.stages import number


def comparable(left, right, *, annual=False):
    if not left or not right:
        return False
    if left.get('basis') != right.get('basis') or left.get('period_kind') != right.get('period_kind'):
        return False
    if annual:
        try:
            consecutive = abs(int(left['period']) - int(right['period'])) == 1
        except (KeyError, TypeError, ValueError):
            return False
        return left.get('period_kind') == 'ANNUAL' and consecutive
    return left.get('period_slot') == right.get('period_slot') and left.get('period_slot') is not None


def ratio(numerator, denominator):
    n, d = number(numerator), number(denominator)
    if n is None or d is None or d <= 0:
        return {'value': None, 'status': 'MANUAL_CHECK'}
    return {'value': n / d, 'status': 'OK'}


def snapshot_signals(snapshot, filed_at):
    """indicators = {signal_type: {status, detail, comparison?}}. Never interpret missing as zero."""
    from risk_engine.loader import load_rules
    allowed = load_rules()['yellow']
    signals = []
    for st, item in snapshot.indicators.items():
        if st not in allowed:
            continue
        detail = dict(item.get('detail') or {}) if item.get('status') == 'OK' else {}
        if st == 'OPERATING_CASH_FLOW' and snapshot.period_kind != 'ANNUAL':
            detail = {}
        signals.append({'signal_type': st, 'event_id': f'financial:{snapshot.corp_code}:{st}',
                        'rcept_no': snapshot.rcept_no, 'filed_at': filed_at,
                        'evidence': snapshot.sources, 'detail': detail})
    return signals
