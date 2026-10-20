"""Harness 평가: 설정을 바꾸기 전/후 결과표를 eval/results/*.md로 저장한다.

사용법 (레포 루트에서):
    uv run python eval/run_eval.py                 # 지금 돌릴 수 있는 실험 전부
    uv run python eval/run_eval.py --only guardrail

실험 상태
- guardrail : 오프라인으로 돈다 (샘플 초안 + harness/guardrails.py).
- context   : 오프라인으로 돈다 (도구 결과 자르기 전/후 토큰 수).
- combo     : 조합 규칙 켬/끔 → 첫 경보가 사건보다 며칠 먼저인지. business-mcp 규칙(B)과 공시 데이터가 있어야 한다.
- rag_k     : RAG k=3/5 → 근거 인용 정확도, 토큰 수. rag-mcp(B)와 Agent(A)가 있어야 한다.
- model     : 기본/대체 모델 → 비용, 지연. 실제 API 키와 Agent가 있어야 한다.
(combo, rag_k, model은 의존 모듈이 준비되면 EXPERIMENTS에 함수만 채우면 된다. 지금은 '미실행'으로 표에 남는다.)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import context, guardrails  # noqa: E402

RESULTS = ROOT / "eval" / "results"

# 금지 표현이 섞인 초안 (LLM이 쓸 법한 문장). 가드레일 없이 그대로 나가면 몇 건이 문제인지 센다.
SAMPLE_DRAFTS = {
    "C001": "{고객명}님, 보유하신 종목에 감사의견 거절 공시가 있어 안내드립니다. 상황을 함께 살펴보시죠.",
    "C002": "{고객명}님, 해당 종목은 곧 상장폐지될 수 있으니 지금 매도하세요.",
    "C003": "{고객명}님, 신용거래 중이라 반드시 비중을 줄이셔야 합니다.",
    "C004": "{고객명}님, 공시 내용을 안내드립니다. 확실히 손실을 피하려면 연락 부탁드립니다.",
    "C005": "{고객명}님, 관련 공시가 접수되어 연락드립니다. 편하신 시간을 알려주세요.",
    "C006": "{고객명}님, 지금 매수하세요. 저점입니다.",
    "C007": "{고객명}님, 감사의견 관련 공시 내용을 정리해 안내드립니다.",
}


def exp_guardrail() -> list[dict]:
    rows = []
    for enabled in (False, True):
        sent = dict(SAMPLE_DRAFTS)
        flagged = guardrails.check_drafts(sent, enabled=enabled)
        # 켜진 경우: 걸린 초안은 PB에게 가기 전에 재작성/직접 작성 대상이 되므로 나가지 않는다.
        passed = {k: v for k, v in sent.items() if k not in flagged}
        bad_out = sum(1 for v in passed.values() if guardrails.find_forbidden(v))
        rows.append({"설정": f"가드레일 {'켬' if enabled else '끔'}", "초안 수": len(sent),
                     "PB에게 넘어간 초안": len(passed), "금지 표현이 들어간 초안": bad_out})
    return rows


def exp_context() -> list[dict]:
    raw = "공시 원문 조각. " * 800  # 약 6,400자
    rows = []
    for label, text in (("자르기 끔", raw), ("자르기 켬(4,000자)", context.truncate_tool_result(raw))):
        rows.append({"설정": label, "글자 수": len(text), "추정 토큰": context.estimate_tokens(text)})
    return rows


def _not_ready(reason: str):
    def run() -> list[dict]:
        return [{"설정": "—", "상태": f"미실행: {reason}"}]
    return run


EXPERIMENTS = {
    "guardrail": ("가드레일 켬/끔 → 금지 표현이 들어간 안내문 수", exp_guardrail),
    "context": ("도구 결과 자르기 켬/끔 → 입력 토큰 수", exp_context),
    "combo": ("조합 규칙 켬/끔 → 첫 경보가 사건보다 며칠 먼저", _not_ready("business-mcp 규칙(B)과 data/disclosures 필요")),
    "rag_k": ("RAG k=3 / k=5 → 근거 인용 정확도, 토큰 수", _not_ready("rag-mcp(B)와 Agent(A) 필요")),
    "model": ("기본 / 대체 모델 → 비용, 지연", _not_ready("Agent(A)와 API 키 필요")),
}


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
    for name, (title, fn) in EXPERIMENTS.items():
        if args.only and name != args.only:
            continue
        rows = fn()
        table = to_markdown(rows)
        if "상태" in rows[0]:  # 미실행 실험은 결과 파일을 만들지 않는다
            print(f"## {title}\n{table}\n")
            continue
        out = RESULTS / f"{name}.md"
        out.write_text(f"# {title}\n\n실행: {datetime.now():%Y-%m-%d %H:%M}\n\n{table}\n", encoding="utf-8")
        print(f"## {title}\n{table}\n→ {out.relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
