"""Versioned references to R3-produced financial snapshots, without fake quotes."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from shared.facts import facts_hash


class FinancialEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['financial'] = 'financial'
    corp_code: str
    rcept_no: str
    period: str
    basis: Literal['CONSOLIDATED', 'SEPARATE']
    revision: int = Field(ge=0)
    indicator: str
    values: dict
    source_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


def snapshot_reference(snapshot, indicator):
    item = snapshot.indicators[indicator]
    identity = {key: getattr(snapshot, key) for key in ('corp_code', 'rcept_no', 'period', 'basis', 'revision')}
    digest = facts_hash({**identity, 'indicator': indicator, 'item': item, 'sources': snapshot.sources})
    return FinancialEvidence(**identity, indicator=indicator,
        values=dict(item.get('detail') or {}), source_hash=digest).model_dump()
