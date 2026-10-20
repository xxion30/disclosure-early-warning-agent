"""rules.yaml 읽기. RULES_PATH 환경 변수로 다른 규칙표(평가용 복사본)를 쓸 수 있다."""
import os
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent / "rules.yaml"


@lru_cache
def _load(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_rules() -> dict:
    return _load(os.environ.get("RULES_PATH") or str(DEFAULT_PATH))
