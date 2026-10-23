"""기준값 변경 전후 비교: 같은 테스트 사례(R3·R5)를 규칙 설정만 바꿔 다시 채점한다. AI·DART 호출 없음.

    uv run python scripts/demo/compare_thresholds.py            # 비교표를 화면에 출력
    uv run python scripts/demo/compare_thresholds.py --write    # docs/verification.md의 비교 칸도 바꿈

risk_engine/rules.yaml(R4 담당)은 고치지 않는다. 설정마다 임시 사본을 만들고 RULES_PATH로 그 사본을 읽게 한다.
비교 항목은 설계서 STEP 06 예시: 기준값(🟠 4 · 🟡 5 · 복합 1.5 · 관찰 75%) 변경, 복합 규칙 켬/끔, 관찰 표시 켬/끔.
"""
from __future__ import annotations

import argparse
import copy
import os
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_cases as ev  # noqa: E402
from risk_engine.loader import DEFAULT_PATH  # noqa: E402

START, END = "<!-- r5-compare:start -->", "<!-- r5-compare:end -->"
OFF = 999  # 기준을 닿을 수 없게 두어 그 규칙을 끈다

# (이름, 설명, thresholds 바꿀 값). 숫자는 규칙 설정값이고 PB 화면에는 나오지 않는다
VARIANTS = [
    ("지금 기준", "🟠 4 · 🟡 5 · 복합 1.5 · 관찰 75%", {}),
    ("🟠 기준 3", "사건 경보를 더 쉽게", {"orange": 3}),
    ("🟠 기준 5", "사건 경보를 더 어렵게", {"orange": 5}),
    ("🟡 기준 4", "재무 경보를 더 쉽게", {"yellow": 4}),
    ("🟡 기준 6", "재무 경보를 더 어렵게", {"yellow": 6}),
    ("복합 1.25", "복합 위험을 더 쉽게", {"composite": 1.25}),
    ("복합 2.0", "복합 위험을 더 어렵게", {"composite": 2.0}),
    ("복합 규칙 끔", "사건·재무 합산 경보 없음", {"composite": OFF}),
    ("관찰 50%", "관찰 중을 더 넓게", {"watch_ratio": 0.5}),
    ("관찰 표시 끔", "관찰 중 없음", {"watch_ratio": OFF}),
]


def run_variant(overrides: dict, cases: list[dict], tmpdir: Path, name: str) -> dict:
    rules = yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8"))
    variant = copy.deepcopy(rules)
    variant["thresholds"].update(overrides)
    path = tmpdir / f"rules_{abs(hash(name))}.yaml"
    path.write_text(yaml.safe_dump(variant, allow_unicode=True, sort_keys=False), encoding="utf-8")
    old = os.environ.get("RULES_PATH")
    os.environ["RULES_PATH"] = str(path)
    try:
        return ev.summarize(ev.evaluate(cases))
    finally:
        if old is None:
            os.environ.pop("RULES_PATH", None)
        else:
            os.environ["RULES_PATH"] = old


def compare() -> list[tuple[str, str, dict]]:
    cases = ev.load_cases()
    with tempfile.TemporaryDirectory() as tmp:
        return [(name, desc, run_variant(over, cases, Path(tmp), name)) for name, desc, over in VARIANTS]


def frac(pair):
    return f"{pair[0]} / {pair[1]}"


def stage_short(code):
    return {"RED": "🔴", "ORANGE": "🟠", "YELLOW": "🟡", "COMPOSITE": "복합", "WATCH": "관찰", "NONE": "없음"}.get(code, code)


def render(rows: list[tuple[str, str, dict]]) -> str:
    base = rows[0][2]
    L = ["_생성: `uv run python scripts/demo/compare_thresholds.py --write` · 같은 사례 50건을 규칙 설정만 바꿔 다시 채점 · rules.yaml은 그대로_", ""]
    L.append("| 설정 | 단건 정답 | 기간 계산 정답 | 적중 | 오탐 | 주당 경보 | 금양 최초 전체 경보(재생) | 금양 최초 사건 경보(🔴·🟠) | CJ CGV | 현대차 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for name, desc, m in rows:
        lead = m["lead_days"].get("r5-timeline-001")
        event_lead = m["event_lead_days"].get("r5-timeline-001")
        first = f"{stage_short(m['first_alert_stage']['r5-timeline-001'])} {lead}일 전" if lead is not None else "없음"
        note = m["first_alert_note"].get("r5-timeline-001")
        if note:
            first += f" · {note}"
        event = f"{stage_short(m['first_event_alert_stage']['r5-timeline-001'])} {event_lead}일 전" if event_lead is not None else "없음"
        first_diff = " ←" if (lead, m["first_alert_stage"]["r5-timeline-001"]) != (base["lead_days"]["r5-timeline-001"], base["first_alert_stage"]["r5-timeline-001"]) else ""
        event_diff = " ←" if (event_lead, m["first_event_alert_stage"]["r5-timeline-001"]) != (base["event_lead_days"]["r5-timeline-001"], base["first_event_alert_stage"]["r5-timeline-001"]) else ""
        diff = lambda key: "" if m[key] == base[key] else " ←"  # noqa: E731
        L.append(
            f"| **{name}**<br><small>{desc}</small> | {frac(m['single'])}{diff('single')} | {frac(m['expected'])}{diff('expected')} | "
            f"{frac(m['hits'])}{diff('hits')} | {frac(m['false_alarms'])}{diff('false_alarms')} | {m['weekly']:.2f} | "
            f"{first}{first_diff} | {event}{event_diff} | {stage_short(m['final'].get('r5-timeline-002'))} | "
            f"{' + '.join(stage_short(a) for a in m['active'].get('r5-normal-001', []))} |"
        )
    L.append("")
    L.append("← 표시는 지금 기준과 달라진 칸이다. 금양 선행 기간은 평가 기간 안의 최초 전체 경보와 최초 사건 경보(🔴·🟠)를 감사의견 거절 공시일(2025-03-21)과 각각 비교한 달력 일수다. 저장 데이터 재생의 🟡 기대값은 R3 검증 대기이며, 종류·날짜·일수가 다른 계산 결과는 '기대값과 다름'으로 구분한다. 운영 실적이 아니다. CJ CGV는 유상증자 날의 맨 위 단계, 현대차는 기준일에 켜진 단계다.")
    L.append("")
    changed = [(name, m) for name, _, m in rows[1:] if any(m[key] != base[key] for key in
               ("false_alarm_ids", "hits", "single", "expected", "lead_days", "event_lead_days"))]
    if changed:
        L.append("**지금 기준과 결과가 달라지는 설정**")
        L.append("")
        for name, m in changed:
            parts = []
            if m["hits"] != base["hits"]:
                parts.append(f"적중 {frac(base['hits'])} → {frac(m['hits'])}")
            if m["false_alarm_ids"] != base["false_alarm_ids"]:
                parts.append(f"오탐 {', '.join(base['false_alarm_ids']) or '없음'} → {', '.join(m['false_alarm_ids']) or '없음'}")
            if m["single"] != base["single"]:
                parts.append(f"단건 정답 {frac(base['single'])} → {frac(m['single'])}")
            if m["expected"] != base["expected"]:
                parts.append(f"기간 계산 정답 {frac(base['expected'])} → {frac(m['expected'])}")
            for key, label in (("lead_days", "최초 전체"), ("event_lead_days", "최초 사건")):
                for cid in base[key]:
                    if m[key].get(cid) != base[key].get(cid):
                        before, after = base[key].get(cid), m[key].get(cid)
                        parts.append(f"{cid} {label} 선행 기간 "
                                     f"{str(before) + '일' if before is not None else '없음'} → "
                                     f"{str(after) + '일' if after is not None else '없음'}")
            L.append(f"- {name}: " + " · ".join(parts))
        L.append("")
    return "\n".join(L)


def write_block(text: str) -> None:
    doc = ev.VERIFICATION.read_text(encoding="utf-8")
    block = f"{START}\n{text}\n{END}"
    if START in doc and END in doc:
        doc = doc[: doc.index(START)] + block + doc[doc.index(END) + len(END):]
    else:
        doc = doc.rstrip() + "\n\n### 기준값 변경 전후 비교 (규칙 엔진만)\n" + block + "\n"
    ev.VERIFICATION.write_text(doc, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="docs/verification.md의 비교 칸을 바꾼다")
    args = ap.parse_args()
    text = render(compare())
    print(text)
    if args.write:
        write_block(text)
        print(f"\n저장: {ev.VERIFICATION.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
