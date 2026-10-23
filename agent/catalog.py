"""Classification vocabulary supplied by R4; contains no scoring logic.

The optional YAML reader only preserves the old assembly's no-argument API.
New Phase 1 bindings inject their classification directly and never use it.
"""
from dataclasses import dataclass
import os
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml


@dataclass(frozen=True)
class SignalCatalog:
    allowed: frozenset[str]
    by_type: Mapping[str, frozenset[str]]

    @classmethod
    def from_document(cls, document):
        allowed = frozenset(document.get("group_a", {})) | frozenset(document.get("group_b", {}))
        if not allowed:
            raise ValueError("R4가 제공한 허용 신호 카탈로그가 필요합니다")
        by_type = MappingProxyType({key: frozenset(value)
                                   for key, value in document.get("allowed_signals_by_type", {}).items()})
        if any(not values <= allowed for values in by_type.values()):
            raise ValueError("카탈로그에 없는 허용 신호")
        return cls(allowed, by_type)


def legacy_document(path=None):
    """Compatibility only. R4 should pass a document/catalog at assembly time.

    No import of the risk engine, no score calculation and no copied constants.
    RULES_PATH is honored in both legacy classification and signal validation.
    """
    target = path or os.environ.get("RULES_PATH") or Path(__file__).resolve().parents[1] / "risk_engine/rules.yaml"
    document = yaml.safe_load(Path(target).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("분류 카탈로그 문서 형식 오류")
    return document
