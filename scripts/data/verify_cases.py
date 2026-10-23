"""eval/cases/r3_cases.json의 모든 공시가 DART에 실제로 있는지 다시 확인한다 (접수번호·접수일·제목).

    uv run python scripts/data/verify_cases.py              # 전부 확인
    uv run python scripts/data/verify_cases.py r3-red-001   # 사례 하나만

DART_API_KEY가 필요하다 (.env). 읽기 전용 호출이다. 하나라도 다르면 종료 코드 1.
정상 사례(NORMAL)는 12개월 목록에 🔴 제목이 없는지도 다시 검사한다.
공시 내용·접수번호를 지어내지 않는다는 규칙을 지키는 장치다 — 사례를 고치면 이 스크립트로 확인한다.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from collector.dart_client import DartClient  # noqa: E402

CASES = ROOT / "eval" / "cases" / "r3_cases.json"
collapse = lambda t: re.sub(r"\s+", " ", t).strip()  # noqa: E731
RED_TITLE = re.compile(r"영업정지|해산사유|관리절차|회생절차|부도|관리종목|횡령|배임|매매거래정지|상장적격성|상장폐지")
ORANGE_TITLE = re.compile(r"유상증자|전환사채|신주인수권부사채|교환사채|감자|소송|합병|분할|양수|양도|풋백옵션|자기주식처분|해외증권시장.*상장폐지")


def evidence_of(case: dict) -> list[dict]:
    return [*case.get("evidence", []), *case.get("steps", [])]


async def verify(cases: list[dict]) -> list[str]:
    problems: list[str] = []
    async with DartClient.from_settings() as client:
        day_cache: dict[tuple[str, str], list[dict]] = {}
        for case in cases:
            for ev in evidence_of(case):
                key = (case["corp_code"], ev["filed_at"])
                if key not in day_cache:
                    day = ev["filed_at"].replace("-", "")
                    day_cache[key] = await client.list_disclosures(case["corp_code"], day, day)
                hit = next((r for r in day_cache[key] if r["rcept_no"] == ev["rcept_no"]), None)
                if hit is None:
                    problems.append(f"{case['case_id']}: {ev['rcept_no']}가 {ev['filed_at']} {case['corp_name']} 목록에 없다")
                elif collapse(hit["report_nm"]) != ev["title"]:
                    problems.append(f"{case['case_id']}: {ev['rcept_no']} 제목이 다르다 — 사례 `{ev['title']}` / DART `{collapse(hit['report_nm'])}`")
            if case["group"] == "NORMAL":
                end = date.fromisoformat(case["window"]["to"])
                start = date.fromisoformat(case["window"]["from"])
                rows = await client.list_disclosures(case["corp_code"], start, end)
                red = [collapse(r["report_nm"]) for r in rows
                       if RED_TITLE.search(r["report_nm"]) and not ORANGE_TITLE.search(r["report_nm"]) and "(종속회사" not in r["report_nm"]]
                if red:
                    problems.append(f"{case['case_id']}: 정상 사례인데 12개월 목록에 🔴 제목이 있다 — {red[:3]}")
    return problems


def main() -> int:
    doc = json.loads(CASES.read_text(encoding="utf-8"))
    only = set(sys.argv[1:])
    cases = [c for c in doc["cases"] if not only or c["case_id"] in only]
    if only and len(cases) != len(only):
        print("없는 사례:", sorted(only - {c["case_id"] for c in cases}))
        return 2
    problems = asyncio.run(verify(cases))
    count = sum(len(evidence_of(c)) for c in cases)
    if problems:
        print(f"확인 실패 {len(problems)}건:")
        print("\n".join(f"  - {p}" for p in problems))
        return 1
    print(f"사례 {len(cases)}개, 공시 {count}건이 모두 DART 목록과 일치합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
