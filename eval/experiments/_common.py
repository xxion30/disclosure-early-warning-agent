"""실험들이 함께 쓰는 보조 코드. (담당: R4)"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = ROOT / "eval" / "fixtures" / "signals.json"
NOT_READY_FIXTURE = [{"설정": "—", "상태": "미실행: eval/capture_signals.py로 신호를 먼저 저장하세요"}]


def load_fixture() -> dict | None:
    """eval/capture_signals.py가 저장한 신호. 없으면 None."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["companies"] if FIXTURE.exists() else None
