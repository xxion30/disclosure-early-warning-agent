"""Validate financial period/value contracts; collection/formulas are R3's responsibility."""
import re
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


def select_snapshot_signals(snapshots, filing_dates):
    """Select per-indicator periods, then consolidated basis, then latest correction."""
    candidates = {}
    for snapshot in snapshots:
        if snapshot.basis not in ('CONSOLIDATED', 'SEPARATE') or snapshot.period_kind not in ('ANNUAL', 'QUARTER', 'YTD'):
            continue
        match = re.fullmatch(r'(\d{4})(?:-Q([1-4]))?', snapshot.period)
        if match is None or (snapshot.period_kind == 'ANNUAL') != (match.group(2) is None):
            raise ValueError('재무 스냅샷 기간 형식이 기준과 일치하지 않습니다.')
        period_end = (int(match.group(1)), int(match.group(2) or '4'), snapshot.period_kind == 'ANNUAL')
        for signal in snapshot_signals(snapshot, filing_dates[snapshot.rcept_no]):
            st = signal['signal_type']
            if st == 'OPERATING_CASH_FLOW' and snapshot.period_kind != 'ANNUAL':
                continue
            # For the same reporting period choose consolidated before separate, even if ingested earlier.
            key = (period_end, snapshot.basis == 'CONSOLIDATED', snapshot.revision,
                   filing_dates[snapshot.rcept_no], snapshot.rcept_no)
            if st not in candidates or key > candidates[st][0]:
                candidates[st] = (key, signal)
    # Compare both bases in the latest period. Never add their scores together.
    cap = []
    for snapshot in snapshots:
        if snapshot.basis in ('CONSOLIDATED','SEPARATE') and 'CAPITAL_IMPAIRMENT' in snapshot.indicators and snapshot.period_kind in ('ANNUAL', 'QUARTER', 'YTD'):
            match = re.fullmatch(r'(\d{4})(?:-Q([1-4]))?', snapshot.period)
            if match:
                cap.append(((int(match[1]), int(match[2] or '4'), snapshot.period_kind == 'ANNUAL'), snapshot))
    if cap:
        latest = max(period for period, _ in cap)
        checks, valid, selected = [], [], []
        for basis, fs_div in [('SEPARATE', 'OFS'), ('CONSOLIDATED', 'CFS')]:
            eligible = [snap for period, snap in cap if period == latest and snap.basis == basis]
            if not eligible:
                checks.append({'fs_div': fs_div, 'status': 'MISSING', 'impairment_pct': None,
                               'period': None, 'rcept_no': None, 'revision': None})
                continue
            chosen = max(eligible, key=lambda snap: (snap.revision, filing_dates[snap.rcept_no], snap.rcept_no))
            selected.append(chosen)
            item = chosen.indicators['CAPITAL_IMPAIRMENT']
            value = number((item.get('detail') or {}).get('impairment_pct')) if item.get('status') == 'OK' else None
            checks.append({'fs_div': fs_div, 'status': 'OK' if value is not None else 'MISSING',
                           'impairment_pct': value, 'period': chosen.period,
                           'rcept_no': chosen.rcept_no, 'revision': chosen.revision})
            if value is not None:
                valid.append((value, fs_div, chosen))
        if valid:
            _, fs_div, chosen = max(valid, key=lambda entry: (entry[0], entry[1]))
        else:
            chosen = max(selected, key=lambda snap: (snap.revision, filing_dates[snap.rcept_no], snap.rcept_no))
            fs_div = None
        signal = next(sig for sig in snapshot_signals(chosen, filing_dates[chosen.rcept_no])
                      if sig['signal_type'] == 'CAPITAL_IMPAIRMENT')
        signal['detail'] = {**(signal['detail'] if valid else {}), 'fs_div': fs_div, 'basis_checks': checks}
        candidates['CAPITAL_IMPAIRMENT'] = ((), signal)
    return [candidates[st][1] for st in sorted(candidates)]
