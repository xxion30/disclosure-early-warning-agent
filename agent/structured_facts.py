"""Validate R3 JSON data without computing financial indicators or risk points."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from agent.ports import AgentError

STATUSES = {'PRESENT', 'MISSING', 'NOT_APPLICABLE', 'NEEDS_CHECK'}


def canonical_facts_hash(payload):
    """R4 facts_hash wire contract; independent of DB and document hashing."""
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()
    except (ValueError, TypeError) as exc:
        raise AgentError('구조화 사실 JSON·유한값 오류') from exc
    return hashlib.sha256(encoded).hexdigest()


def validate_facts(raw, disclosure):
    """Validate R4 ToolResponse.data; direct payloads remain unit-port compatible."""
    if isinstance(raw, dict) and ('availability' in raw or 'structured_facts' in raw):
        if any(raw.get(key) != disclosure[key] for key in ('rcept_no', 'corp_code')):
            raise AgentError('구조화 사실 조회 공시·기업 불일치')
        if raw.get('availability') != 'AVAILABLE':
            raise AgentError('구조화 사실 이용 불가: 위험 없음으로 처리할 수 없습니다')
        payload, digest = raw.get('structured_facts'), raw.get('facts_hash')
        if not isinstance(payload, dict):
            raise AgentError('AVAILABLE 구조화 사실 누락')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise AgentError('구조화 사실 facts_hash 누락·형식 오류')
        if canonical_facts_hash(payload) != digest:
            raise AgentError('구조화 사실 내용·facts_hash 불일치')
        result = _validate_payload(payload, disclosure, wire=True)
        result['facts_hash'] = digest
        return result
    return _validate_payload(raw, disclosure, wire=False)


def _validate_payload(raw, disclosure, *, wire):
    if not isinstance(raw, dict) or raw.get('facts') is None:
        raise AgentError('구조화 사실 미수집·수집 실패: 정상 빈 결과와 구분 필요')
    if raw.get('rcept_no') != disclosure['rcept_no'] or raw.get('corp_code') != disclosure['corp_code']:
        raise AgentError('구조화 사실 공시·기업 불일치')
    if not raw.get('schema_version') or not isinstance(raw['facts'], list):
        raise AgentError('구조화 사실 스키마 버전·목록 필요')
    if wire and raw['schema_version'] != '1':
        raise AgentError('지원하지 않는 구조화 사실 스키마 버전')
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
        allowed = {'string', 'number', 'integer', 'boolean'} if wire else {
            'string', 'number', 'integer', 'decimal', 'boolean', 'date'}
        if dtype not in allowed:
            raise AgentError('구조화 사실 자료형 오류')
        if any(not isinstance(source.get(k), str) or not source[k].strip() for k in ('api', 'field', 'rcept_no', 'fetched_at')):
            raise AgentError('API·필드·접수번호·조회 시각 필요')
        if source['rcept_no'] != disclosure['rcept_no']:
            raise AgentError('API 출처 접수번호 불일치')
        try:
            if datetime.fromisoformat(source['fetched_at']).tzinfo is None:
                raise ValueError()
        except ValueError as exc:
            raise AgentError('API 조회 시각·시간대 필요') from exc
        row_key = source.get('row_key', fact.get('row_key'))
        if row_key is not None and not isinstance(row_key, str):
            raise AgentError('API 행 식별자는 문자열이어야 합니다')
        if source.get('basis') not in (None, 'OFS', 'CFS') or (
                source.get('period') is not None and not isinstance(source['period'], str)):
            raise AgentError('API 기간·재무제표 구분 오류')
        key = (name, source['api'], source['field'], row_key, source.get('period'), source.get('basis'))
        if key in seen:
            raise AgentError('API 다중 행 식별 불가·중복')
        seen.add(key)
        value = fact.get('value')
        if fact['status'] == 'PRESENT':
            if value is None or isinstance(value, (dict, list)):
                raise AgentError('PRESENT 값 누락·자료형 오류')
            if dtype == 'boolean' and not isinstance(value, bool):
                raise AgentError('불리언 값 오류')
            if dtype in {'number', 'decimal', 'integer'}:
                try:
                    if isinstance(value, bool):
                        raise ValueError()
                    if dtype == 'number' and type(value) not in (int, float):
                        raise ValueError()
                    if wire and dtype == 'integer' and type(value) is not int:
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
