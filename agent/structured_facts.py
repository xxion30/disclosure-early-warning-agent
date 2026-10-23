"""Validate R3 JSON data without computing financial indicators or risk points."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from agent.ports import AgentError

STATUSES = {'PRESENT', 'MISSING', 'NOT_APPLICABLE', 'NEEDS_CHECK'}


def validate_facts(raw, disclosure):
    """Collector-proposed keys are accepted, unknown raw blobs are not retained.

    R4 owns the final shared schema. This validation accepts the documented
    facts schema and preserves source-specific metadata, not full responses.
    """
    if not isinstance(raw, dict) or raw.get('facts') is None:
        raise AgentError('구조화 사실 미수집·수집 실패: 정상 빈 결과와 구분 필요')
    if raw.get('rcept_no') != disclosure['rcept_no'] or raw.get('corp_code') != disclosure['corp_code']:
        raise AgentError('구조화 사실 공시·기업 불일치')
    if not raw.get('schema_version') or not isinstance(raw['facts'], list):
        raise AgentError('구조화 사실 스키마 버전·목록 필요')
    try:
        collected = datetime.fromisoformat(raw['collected_at'])
        if collected.tzinfo is None:
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentError('수집 시각·시간대 필요') from exc
    result = {k: deepcopy(raw[k]) for k in (
        'schema_version', 'rcept_no', 'corp_code', 'collected_at',
        'data_version', 'revision', 'content_hash') if k in raw}
    facts, seen = [], set()
    for fact in raw['facts']:
        if not isinstance(fact, dict) or fact.get('status') not in STATUSES:
            raise AgentError('구조화 사실 상태 오류')
        name, dtype, source = fact.get('name'), fact.get('dtype'), fact.get('source')
        if not isinstance(name, str) or not name.strip() or not isinstance(source, dict):
            raise AgentError('항목 코드·API 출처 필요')
        if dtype not in {'string', 'integer', 'decimal', 'boolean', 'date'}:
            raise AgentError('구조화 사실 자료형 오류')
        if any(not isinstance(source.get(k), str) or not source[k].strip() for k in ('api', 'field', 'rcept_no', 'fetched_at')):
            raise AgentError('API·필드·접수번호·조회 시각 필요')
        if source['rcept_no'] != disclosure['rcept_no']:
            raise AgentError('API 출처 접수번호 불일치')
        key = (name, source['api'], source['field'], source.get('row_key', fact.get('row_key')))
        if key[-1] is not None and not isinstance(key[-1], str):
            raise AgentError('API 행 식별자는 문자열이어야 합니다')
        if key in seen:
            raise AgentError('API 다중 행 식별 불가·중복')
        seen.add(key)
        value = fact.get('value')
        if fact['status'] == 'PRESENT':
            if value is None or isinstance(value, (dict, list)):
                raise AgentError('PRESENT 값 누락·자료형 오류')
            if dtype == 'boolean' and not isinstance(value, bool):
                raise AgentError('불리언 값 오류')
            if dtype in {'decimal', 'integer'}:
                try:
                    if isinstance(value, bool):
                        raise ValueError()
                    number = Decimal(str(value))
                    if not number.is_finite() or (dtype == 'integer' and number != number.to_integral_value()):
                        raise ValueError()
                except (InvalidOperation, ValueError) as exc:
                    raise AgentError('정규화 수치 오류') from exc
            if dtype in {'string', 'date'} and (not isinstance(value, str) or not value.strip()):
                raise AgentError('문자열·날짜 값 오류')
            if dtype == 'date':
                from datetime import date
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise AgentError('날짜 형식 오류') from exc
        elif value is not None:
            raise AgentError('결측·확인 필요 값을 확정값으로 전달할 수 없습니다')
        if fact['status'] != 'PRESENT' and not fact.get('reason'):
            raise AgentError('결측·제외·확인 필요 사유 누락')
        clean = {k: deepcopy(fact[k]) for k in (
            'name', 'dtype', 'raw', 'value', 'unit', 'status', 'reason', 'row_key',
            'period', 'basis', 'data_version') if k in fact}
        if isinstance(clean.get('raw'), (dict, list)):
            raise AgentError('API 전체 응답은 raw에 저장할 수 없습니다')
        clean['source'] = {k: deepcopy(source[k]) for k in (
            'api', 'field', 'rcept_no', 'fetched_at', 'row_key', 'period', 'basis', 'data_version') if k in source}
        facts.append(clean)
    result['facts'] = facts
    return result


def api_evidence(raw, facts):
    """Return a verified API reference, never a fabricated document quote."""
    if not isinstance(raw, dict) or raw.get('kind') != 'api':
        return None
    keys = ('api', 'field', 'rcept_no', 'row_key', 'value', 'unit', 'period', 'basis', 'data_version')
    for fact in facts.get('facts', []):
        if fact['status'] != 'PRESENT':
            continue
        source = fact['source']
        expected = {k: source.get(k, fact.get(k, facts.get(k))) for k in keys}
        # Explicit matching prevents model-created row identifiers or values.
        if all(k in raw and raw[k] == expected[k] for k in keys):
            return {'kind': 'api', **deepcopy(expected)}
    raise AgentError('검증되지 않은 API 근거')
