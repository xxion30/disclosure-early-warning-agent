"""A reads classification configuration; business scoring remains with B."""
from pathlib import Path

import yaml


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
