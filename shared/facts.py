"""Versioned API facts. These are data, never fabricated document quotations."""
import hashlib
import json
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class FactSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    api: str = Field(min_length=1)
    field: str = Field(min_length=1)
    rcept_no: str = Field(pattern=r'^\d{14}$')
    fetched_at: datetime
    row_key: str | None = None
    period: str | None = None
    basis: Literal['OFS', 'CFS'] | None = None

    @model_validator(mode='after')
    def timezone_required(self):
        if self.fetched_at.tzinfo is None:
            raise ValueError('fetched_at에는 시간대가 필요합니다.')
        return self


class StructuredFact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]*$')
    dtype: Literal['string', 'number', 'integer', 'boolean']
    raw: Any = None
    value: Any = None
    unit: str | None = None
    status: Literal['PRESENT', 'MISSING', 'NOT_APPLICABLE', 'NEEDS_CHECK']
    reason: str | None = None
    source: FactSource

    @model_validator(mode='after')
    def valid_value(self):
        if self.raw is not None and not isinstance(self.raw, (str, int, float, bool)):
            raise ValueError('raw는 원천 필드의 스칼라 값이어야 합니다.')
        if self.status != 'PRESENT':
            if self.value is not None:
                raise ValueError('확정되지 않은 value는 null이어야 합니다.')
            if not self.reason:
                raise ValueError('결측/제외/확인 필요 사유가 필요합니다.')
            return self
        kinds = {'string': (str,), 'number': (int, float), 'integer': (int,), 'boolean': (bool,)}
        if type(self.value) not in kinds[self.dtype]:
            raise ValueError('dtype와 value가 일치하지 않습니다.')
        if isinstance(self.value, float):
            from math import isfinite
            if not isfinite(self.value):
                raise ValueError('유한한 값이 필요합니다.')
        return self


class StructuredFacts(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['1'] = '1'
    rcept_no: str = Field(pattern=r'^\d{14}$')
    corp_code: str = Field(min_length=1)
    collected_at: datetime
    facts: list[StructuredFact]

    @model_validator(mode='after')
    def valid_identity(self):
        if self.collected_at.tzinfo is None:
            raise ValueError('collected_at에는 시간대가 필요합니다.')
        keys = []
        for fact in self.facts:
            if fact.source.rcept_no != self.rcept_no:
                raise ValueError('사실과 출처의 접수번호가 다릅니다.')
            keys.append((fact.name, fact.source.api, fact.source.field, fact.source.row_key,
                         fact.source.period, fact.source.basis))
        if len(keys) != len(set(keys)):
            raise ValueError('행 식별자를 포함한 사실 키가 중복됩니다.')
        return self


def facts_hash(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


API_REFERENCE_KEYS = ('api', 'field', 'rcept_no', 'row_key', 'value', 'unit', 'period', 'basis', 'data_version')


def api_reference(fact, payload):
    return {'kind': 'api', **{key: fact['source'].get(key, fact.get(key, payload.get(key)))
                             for key in API_REFERENCE_KEYS}}


def verify_api_reference(reference, payload):
    if payload is None:
        raise ValueError('API 근거를 대조할 저장된 사실이 없습니다.')
    parsed = StructuredFacts.model_validate(payload).model_dump(mode='json')
    if not any(fact['status'] == 'PRESENT' and type(fact['value']) is type(reference.get('value'))
               and api_reference(fact, parsed) == reference
               for fact in parsed['facts']):
        raise ValueError('API 근거가 저장된 확정 사실과 일치하지 않습니다.')
    return reference


def store_facts(session, rcept_no, payload):
    """R3 writes through this validator inside its collection transaction."""
    from shared.models import Disclosure
    from sqlalchemy import select
    row = session.scalar(select(Disclosure).where(Disclosure.rcept_no == rcept_no).with_for_update())
    if row is None:
        raise ValueError('공시를 찾을 수 없습니다.')
    data = StructuredFacts.model_validate(payload)
    if data.rcept_no != row.rcept_no or data.corp_code != row.corp_code:
        raise ValueError('공시와 구조화 사실의 식별자가 다릅니다.')
    row.structured_facts = data.model_dump(mode='json')
    # Every update must be explicitly handed off after all required data is ready.
    row.analysis_ready_at = None
    session.flush()
    return facts_hash(row.structured_facts)
