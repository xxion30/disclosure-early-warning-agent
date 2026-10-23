"""실험: 모델 비교. 같은 공시·같은 검색 조각을 두 모델에 주고 사실 추출을 비교한다. (담당: R2)"""
from experiments._common import NOT_READY_FIXTURE, ROOT, load_fixture


OWNER = "R2"
TITLE = '모델 비교 (gpt-5.4-mini / gpt-5.4-nano) → 신호 일치, 지연, 토큰'
NOTE = "기준 = 저장된 신호(gpt-5.4-mini로 실행, 최종 결과가 기대 결과와 일치함). '기준과 같음'은 정답률이 아니라 일치율이고, 기준은 재시도와 마스킹을 포함한 전체 흐름의 결과인 반면 여기서는 모델을 한 번만 호출했다. 달러 비용은 가격표를 가정하지 않아 싣지 않고 토큰만 적는다. Anthropic 키가 없어 기본/대체 모델 비교는 같은 회사의 두 모델로 대신했다."


def run() -> list[dict]:
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

    data = load_fixture()
    if not data:
        return NOT_READY_FIXTURE
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
