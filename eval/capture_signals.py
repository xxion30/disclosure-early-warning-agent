"""Record explicitly requested Phase 1 runs using an R4 dependency factory.

No seed/reset/server boot, no old replay imports and no overwrite of the old
eval/fixtures/signals.json baseline. The injected model may incur API costs.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def capture_phase1(runtime, receipts: list[str], output: Path) -> list[dict]:
    """Internal evaluation capture. Not gold labels and not a public PB response."""
    if not receipts or any(not isinstance(r, str) or not re.fullmatch(r"\d{14}", r) for r in receipts):
        raise ValueError("실제 접수번호 14자리가 필요합니다")
    if runtime.deps.phase1 is None:
        raise ValueError("Phase 1 규칙·저장 연결이 필요합니다")
    output = Path(output)
    # Do not overwrite prior measurements or the inherited baseline accidentally.
    if output.exists():
        raise FileExistsError("새 평가 결과 경로를 지정하세요")
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for receipt in dict.fromkeys(receipts):
        state = await runtime.start(receipt)
        rows.append({k: state.get(k) for k in (
            "disclosure", "stage", "status", "flags", "score_result", "evidence", "error", "trace_id")})
        output.write_text(json.dumps({
            "note": "내부 평가용 실제 실행 기록. 정답 라벨·PB 공개 응답이 아님",
            "runs": rows,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factory", required=True, help="R4 async/sync Dependencies factory: module:function")
    parser.add_argument("--receipt", required=True, action="append", help="실제 DART 접수번호; 반복 지정")
    parser.add_argument("--output", required=True, type=Path, help="존재하지 않는 새 내부 평가 JSON")
    parser.add_argument("--checkpoint", help="R4가 준비한 checkpoint 파일 또는 Postgres DSN")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("기존 결과를 덮어쓰지 않습니다. 새 --output 경로를 지정하세요")
    if any(not re.fullmatch(r"\d{14}", r) for r in args.receipt):
        parser.error("--receipt는 실제 DART 접수번호 14자리입니다")
    if ":" not in args.factory:
        parser.error("--factory는 module:function 형식입니다")

    async def execute():
        from agent.runtime import open_runtime
        module, name = args.factory.rsplit(":", 1)
        factory = getattr(importlib.import_module(module), name)
        deps = factory()
        if inspect.isawaitable(deps):
            deps = await deps
        if deps.phase1 is None:
            raise ValueError("R4가 Phase1Bindings와 새 저장 도구를 연결한 factory가 필요합니다")
        async with open_runtime(deps, args.checkpoint) as runtime:
            rows = await capture_phase1(runtime, args.receipt, args.output)
        print(f"내부 평가 기록: {len(rows)}건 → {args.output}")

    asyncio.run(execute())


if __name__ == "__main__":
    main()
