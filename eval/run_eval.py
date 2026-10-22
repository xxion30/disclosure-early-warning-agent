"""Harness 평가: 설정을 바꾸기 전/후 결과표를 eval/results/*.md로 저장한다.

사용법 (레포 루트에서):
    uv run python eval/run_eval.py                 # 지금 돌릴 수 있는 실험 전부
    uv run python eval/run_eval.py --only guardrail

실험 상태
- guardrail : 오프라인으로 돈다 (샘플 초안 + harness/guardrails.py).
- context   : 오프라인으로 돈다 (도구 결과 자르기 전/후 토큰 수).
- combo     : 조합 규칙 켬/끔 → 첫 경보가 사건보다 며칠 먼저인지. eval/fixtures/signals.json(eval/capture_signals.py가 저장)을 규칙에 다시 넣는다. AI 호출 없음.
- rag_k     : RAG k=3/5 → 저장된 근거 인용이 검색 조각에 들어 있는 비율, 토큰 수. data/chroma 색인과 OPENAI_API_KEY(질의 임베딩)가 필요하다.
- model     : 두 모델에 같은 공시·검색 조각을 주고 사실 추출을 비교(성공률, 기준과 일치, 지연, 토큰). OPENAI_API_KEY 필요, 진짜 호출. EVAL_MODELS로 모델 변경.
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


FIXTURE = ROOT / "eval" / "fixtures" / "signals.json"


def _fixture() -> dict | None:
    return __import__("json").loads(FIXTURE.read_text(encoding="utf-8"))["companies"] if FIXTURE.exists() else None


def _replay_levels(comp: dict, rules: dict) -> list[dict]:
    """저장된 신호를 같은 순서로 규칙에 다시 넣어 공시마다의 등급을 구한다 (AI 호출 없음)."""
    from risk_engine import risk_rules

    by_rcept: dict[str, list[dict]] = {}
    for sg in comp["signals"]:
        by_rcept.setdefault(sg["rcept_no"], []).append(sg)
    out, history = [], []
    for a in comp["assessments"]:
        if not a["filed_at"]:
            continue
        mine = by_rcept.get(a["rcept_no"], [])
        new = [{"signal_type": g["signal_type"], "detail": g["detail"], "evidence": (g["detail"] or {}).get("evidence"),
                "filed_at": g["filed_at"], "rcept_no": g["rcept_no"]}
               for g in mine if not (g["detail"] or {}).get("synthetic")]  # 규칙이 만든 신호(정정 3회)는 다시 만들게 한다
        res = risk_rules.calculate(base_score=comp["base_score"] or 0, base_reasons=comp["base_reasons"] or [],
                                   history=history, new_signals=new, as_of=a["filed_at"], rules=rules)
        out.append({"rcept_no": a["rcept_no"], "filed_at": a["filed_at"], "level": res["level"],
                    "score": res["total_score"], "stored_level": a["level"], "combo_upgraded": res["combo_upgraded"]})
        history = history + [{**g, "points": g["points"]} for g in mine]
    return out


def exp_combo() -> list[dict]:
    import copy
    from datetime import date

    from risk_engine.loader import load_rules

    data = _fixture()
    if not data:
        return [{"설정": "—", "상태": "미실행: eval/capture_signals.py로 신호를 먼저 저장하세요"}]
    on = load_rules()
    off = copy.deepcopy(on)
    off["combo"]["enabled"] = False
    order = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
    rows = []
    for stock, comp in data.items():
        event = comp.get("event") or {}
        ev_date = (event.get("date") or "")[:10]
        for label, rules in (("조합 규칙 켬", on), ("조합 규칙 끔", off)):
            seq = _replay_levels(comp, rules)
            before = [x for x in seq if not ev_date or x["filed_at"] < ev_date]
            first = next((x for x in before if order[x["level"]] >= order["High"]), None)
            ev = next((x for x in seq if x["rcept_no"] == event.get("rcept_no")), None)
            days = (date.fromisoformat(ev_date) - date.fromisoformat(first["filed_at"])).days if first and ev_date else None
            rows.append({
                "회사": comp["name"], "설정": label,
                "사건 전 첫 High 이상": f"{first['filed_at']} {first['level']}" if first else "없음",
                "사건보다 먼저(일)": days if days is not None else "-",
                "사건 전 High 이상 공시 수": sum(1 for x in before if order[x["level"]] >= order["High"]),
                "조합으로 올린 공시 수": sum(1 for x in seq if x["combo_upgraded"]),
                "사건 공시 등급": ev["level"] if ev else "-",
                "저장된 등급과 일치": f"{sum(1 for x in seq if x['level'] == x['stored_level'])}/{len(seq)}" if rules is on else "-",
            })
    return rows


def exp_rag_k() -> list[dict]:
    import json
    from collections import defaultdict

    from agent.rules import make_classifier
    from mcp_servers.rag.tools import search_disclosure_text

    data = _fixture()
    if not data:
        return [{"설정": "—", "상태": "미실행: eval/capture_signals.py로 신호를 먼저 저장하세요"}]
    classify = make_classifier()
    titles = {}
    for stock in data:
        for d in json.loads((ROOT / "data" / "disclosures" / f"{stock}.json").read_text(encoding="utf-8")):
            titles[d["rcept_no"]] = d["title"]
    # 공시마다: 저장된 신호의 원문 인용(AI가 k=5 조각에서 고른 문장)
    quotes: dict[str, list[str]] = defaultdict(list)
    for comp in data.values():
        for g in comp["signals"]:
            for e in (g["detail"] or {}).get("evidence") or []:
                if isinstance(e, dict) and e.get("quote") and not (g["detail"] or {}).get("synthetic"):
                    quotes[g["rcept_no"]].append(e["quote"])
    cases = [(r, qs) for r, qs in quotes.items() if r in titles and classify(titles[r])]
    rows = []
    for k in (3, 5):
        hit = total = tokens = calls = 0
        for rcept, qs in cases:
            res = search_disclosure_text(rcept, f"{classify(titles[rcept])} 조건 값과 근거 문장", k)
            if not res.get("ok"):
                continue
            chunks = res["data"]["chunks"]
            calls += 1
            tokens += context.estimate_tokens(" ".join(c["text"] for c in chunks))
            for q in set(qs):
                total += 1
                hit += any(q in c["text"] for c in chunks)
        rows.append({"설정": f"RAG k={k}", "검색한 공시 수": calls, "저장된 인용": total,
                     "인용이 검색 조각 안에 있음": hit, "근거 인용 재현율": f"{hit / total:.1%}" if total else "-",
                     "검색당 평균 조각 토큰(추정)": round(tokens / calls) if calls else "-"})
    return rows


def exp_model() -> list[dict]:
    """같은 공시·같은 검색 조각을 두 모델에 주고 사실 추출 결과를 비교한다 (기준 실행 = 저장된 신호)."""
    import asyncio
    import json
    import os
    import time
    from types import SimpleNamespace

    from langchain_core.callbacks import get_usage_metadata_callback

    from agent.model_adapter import KnownSignalsModel, _openai_chat
    from agent.rules import make_classifier
    from agent.llm import StructuredLLM
    from agent.state import FactsOutput
    from mcp_servers.rag.tools import search_disclosure_text
    from shared.config import get_settings

    data = _fixture()
    if not data:
        return [{"설정": "—", "상태": "미실행: eval/capture_signals.py로 신호를 먼저 저장하세요"}]
    st = get_settings()
    models = [m.strip() for m in os.environ.get("EVAL_MODELS", f"{st.LLM_FALLBACK},gpt-5.4-nano").split(",") if m.strip()]
    classify = make_classifier()
    titles = {}
    for stock in data:
        for d in json.loads((ROOT / "data" / "disclosures" / f"{stock}.json").read_text(encoding="utf-8")):
            titles[d["rcept_no"]] = d
    base: dict[str, set[str]] = {}
    for comp in data.values():
        for g in comp["signals"]:
            if not (g["detail"] or {}).get("synthetic"):
                base.setdefault(g["rcept_no"], set()).add(g["signal_type"])
    limit = int(os.environ.get("EVAL_MODEL_CASES", "12"))
    cases = [r for r in base if r in titles and classify(titles[r]["title"])][:limit]

    async def run_one(model_name: str) -> dict:
        llm = KnownSignalsModel(StructuredLLM(_openai_chat(model_name, st.OPENAI_API_KEY),
                                              _openai_chat(model_name, st.OPENAI_API_KEY), SimpleNamespace(recover_llm=lambda p, f: p()), ""))
        same = ok = 0
        secs, tin, tout = [], 0, 0
        for rcept in cases:
            d = titles[rcept]
            dtype = classify(d["title"])
            res = search_disclosure_text(rcept, f"{dtype} 조건 값과 근거 문장", 5)
            if not res.get("ok"):
                continue
            chunks = res["data"]["chunks"]
            payload = {"disclosure": {"rcept_no": rcept, "title": d["title"], "filed_at": d["filed_at"]},
                       "disclosure_type": dtype, "chunks": chunks, "attempt": 1}
            t0 = time.monotonic()
            try:
                with get_usage_metadata_callback() as cb:
                    out = await llm.generate("extract_facts", FactsOutput, payload)
                u = list(cb.usage_metadata.values())
                tin += sum(x.get("input_tokens", 0) for x in u)
                tout += sum(x.get("output_tokens", 0) for x in u)
            except Exception:  # noqa: BLE001 — 실패도 결과의 일부로 센다
                secs.append(time.monotonic() - t0)
                continue
            secs.append(time.monotonic() - t0)
            ok += 1
            same += {f.signal_type for f in out.facts} == base[rcept]
        n = len(cases)
        return {"모델": model_name, "공시 수": n, "응답 성공": f"{ok}/{n}", "신호 종류가 기준과 같음": f"{same}/{n}",
                "평균 지연(초)": round(sum(secs) / len(secs), 1) if secs else "-",
                "입력 토큰": tin, "출력 토큰": tout}

    return [asyncio.run(run_one(m)) for m in models]


def _not_ready(reason: str):
    def run() -> list[dict]:
        return [{"설정": "—", "상태": f"미실행: {reason}"}]
    return run


EXPERIMENTS = {
    "guardrail": ("가드레일 켬/끔 → 금지 표현이 들어간 안내문 수", exp_guardrail),
    "context": ("도구 결과 자르기 켬/끔 → 입력 토큰 수", exp_context),
    "combo": ("조합 규칙 켬/끔 → 첫 경보가 사건보다 며칠 먼저", exp_combo),
    "rag_k": ("RAG k=3 / k=5 → 근거 인용 정확도, 토큰 수", exp_rag_k),
    "model": ("모델 비교 (gpt-5.4-mini / gpt-5.4-nano) → 신호 일치, 지연, 토큰", exp_model),
}


NOTES = {
    "guardrail": "입력은 금지 표현을 일부러 섞어 쓴 샘플 초안 7개다 (실제 모델이 쓴 초안이 아니다). 켜면 걸린 초안 4개는 PB에게 가기 전에 재작성 대상이 된다.",
    "context": "합성 텍스트(약 6,400자)로 잰 값이다. 토큰은 글자 수 기반 추정이다.",
    "combo": ("입력은 eval/fixtures/signals.json (진짜 모델이 실제 DART 공시에서 찾은 신호). 켬 상태로 다시 계산한 등급이 원래 저장된 등급과 모두 같아(마지막 열) 재계산이 실제 실행을 그대로 재현한다.\n\n"
              "**결과: 켬/끔이 같다.** 실제 공시에서는 조합 조건(12개월 안에 서로 다른 신호 유형 3개 이상)을 채운 공시가 한 건도 없어 조합 규칙이 한 번도 발동하지 않았다. "
              "금양·위니아의 조기 경보는 B그룹 점수 누적과 확정 악재 규칙(거래정지)이 만든 것이다. 조합 규칙의 동작 자체는 `tests/mcp_servers/test_rules.py`로 검증한다. "
              "이 데이터로는 조합 규칙의 효과를 주장할 수 없다."),
    "rag_k": ("저장된 인용은 AI가 k=5 검색 결과에서 고른 문장이므로 k=5가 기준선이다. k=5에서도 100%가 아닌 것은 인용이 한 번 더 검색한 결과와 어긋나는 경우가 있어서이며(원인은 따로 조사하지 않았다), "
              "k=3은 k=5보다 재현율이 낮고 조각 토큰은 약 22% 적다. 비용을 줄이려고 k를 줄이면 근거 누락이 늘어난다는 뜻이다."),
    "model": ("기준 = 저장된 신호(gpt-5.4-mini로 실행, 최종 결과가 기대 결과와 일치함). '기준과 같음'은 정답률이 아니라 일치율이고, 기준은 재시도와 마스킹을 포함한 전체 흐름의 결과인 반면 여기서는 모델을 한 번만 호출했다. 달러 비용은 가격표를 가정하지 않아 싣지 않고 토큰만 적는다. "
              "Anthropic 키가 없어 기본/대체 모델 비교는 같은 회사의 두 모델로 대신했다."),
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
        note = f"\n\n> 해석: {NOTES[name]}" if name in NOTES else ""
        out.write_text(f"# {title}\n\n실행: {datetime.now():%Y-%m-%d %H:%M}\n\n{table}{note}\n", encoding="utf-8")
        print(f"## {title}\n{table}\n→ {out.relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
