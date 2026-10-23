"""공시 분류와 신호 입력 연결 (담당: R2). 분류 규칙 표는 risk_engine/rules.yaml, 점수 계산은 R4의 규칙 엔진이 한다."""
from pathlib import Path

import yaml

from agent.catalog import legacy_document


def load_classifier(path: str | Path):
    """Proposed YAML shape: classification: [{type: ..., keywords: [...]}].

    Ordered entries define precedence. B may bind a different classifier instead.
    """
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return _classifier_from_document(document)


def _classifier_from_document(document):
    entries = document["classification"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("classification 규칙이 필요합니다")
    for item in entries:
        if not isinstance(item.get("type"), str) or not item["type"].strip():
            raise ValueError("분류 type 누락")
        keywords = item.get("keywords")
        if not isinstance(keywords, list) or not keywords or any(
            not isinstance(word, str) or not word.strip() for word in keywords
        ):
            raise ValueError("비어 있지 않은 keywords가 필요합니다")

    def classify(title: str) -> str | None:
        return next((entry["type"] for entry in entries
                     if any(word in title for word in entry["keywords"])), None)
    return classify


def signal_input(extracted: dict) -> list[dict]:
    """검증된 사실을 calculate_risk_score의 new_signals로 바꾼다. 점수는 넣지 않는다(B 규칙표가 정한다)."""
    signals = []
    for fact in extracted.get("facts", []):
        evidence = list(fact.get("evidence") or [])
        signals.append({
            "signal_type": str(fact["signal_type"]).strip().upper(),  # 모델이 소문자로 써도 규칙표 이름에 맞춘다
            "detail": dict(fact.get("values") or {}),
            "evidence": evidence,
            "rcept_no": evidence[0]["rcept_no"] if evidence else None,
        })
    return signals


def make_classifier(*, document=None):
    """rules.yaml 제목 키워드 표 + 첨부만 올리는 정정 공시([첨부정정]·[첨부추가])는 해당 없음."""
    document = legacy_document() if document is None else document
    base = _classifier_from_document(document)
    skip = tuple(document.get("skip_title_prefixes", []))

    def classify(title: str):
        if skip and title.lstrip().startswith(skip):
            return None
        return base(title)

    return classify


def make_stage_classifier(entries: list[dict], *, skip_prefixes: tuple[str, ...] = ()):
    """R4가 제공한 새 분류표를 실행한다. 점수 값과 위험 판단 조건은 포함하지 않는다.

    Each entry: {bucket, item, keywords: [str], text_flags: [str]}.
    Unlike the legacy first-match classifier, all applicable routes are retained.
    """
    from agent.phase1 import RiskRoute

    compiled = []
    for entry in entries:
        route = RiskRoute.model_validate({k: entry[k] for k in ("bucket", "item", "text_flags") if k in entry})
        keywords = entry.get("keywords")
        if not isinstance(keywords, list) or not keywords or any(not isinstance(w, str) or not w.strip() for w in keywords):
            raise ValueError("분류 키워드가 필요합니다")
        compiled.append((tuple(keywords), route.model_dump()))

    def classify(title):
        if skip_prefixes and title.lstrip().startswith(skip_prefixes):
            return []
        return [dict(route) for words, route in compiled if any(w in title for w in words)]

    return classify
