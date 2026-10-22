"""평가 실행기: 설정을 바꾸기 전/후 결과표를 eval/results/*.md로 저장한다. (담당: R4)

사용법 (레포 루트에서):
    uv run python eval/run_eval.py                 # 지금 돌릴 수 있는 실험 전부
    uv run python eval/run_eval.py --only guardrail

실험은 eval/experiments/ 아래 파일 하나에 하나씩 있다 (guardrail·context·model = R2, rag_k = R3, combo = R4).
combo·rag_k·model은 eval/capture_signals.py가 저장한 신호(eval/fixtures/signals.json)가 필요하다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # experiments 패키지

from experiments import EXPERIMENTS  # noqa: E402

RESULTS = ROOT / "eval" / "results"


def to_markdown(rows: list[dict]) -> str:
    cols = list(rows[0])
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    lines += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=EXPERIMENTS)
    args = parser.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    for name, (title, fn, note) in EXPERIMENTS.items():
        if args.only and name != args.only:
            continue
        rows = fn()
        table = to_markdown(rows)
        if "상태" in rows[0]:  # 미실행 실험은 결과 파일을 만들지 않는다
            print(f"## {title}\n{table}\n")
            continue
        out = RESULTS / f"{name}.md"
        text = f"\n\n> 해석: {note}" if note else ""
        out.write_text(f"# {title}\n\n실행: {datetime.now():%Y-%m-%d %H:%M}\n\n{table}{text}\n", encoding="utf-8")
        print(f"## {title}\n{table}\n→ {out.relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
