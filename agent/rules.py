"""공시 분류와 신호 입력 연결 (담당: R2). 분류 규칙 표는 risk_engine/rules.yaml, 점수 계산은 R4의 규칙 엔진이 한다."""
from pathlib import Path

import yaml

from risk_engine.loader import DEFAULT_PATH, load_rules


def load_classifier(path: str | Path):
    """Proposed YAML shape: classification: [{type: ..., keywords: [...]}].

    Ordered entries define precedence. B may bind a different classifier instead.
    """
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
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


def make_classifier():
    """rules.yaml 제목 키워드 표 + 첨부만 올리는 정정 공시([첨부정정]·[첨부추가])는 해당 없음."""
    base = load_classifier(DEFAULT_PATH)
    skip = tuple(load_rules().get("skip_title_prefixes", []))

    def classify(title: str):
        if skip and title.lstrip().startswith(skip):
            return None
        return base(title)

    return classify
