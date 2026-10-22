"""실험: RAG k=3/5. 저장된 근거 인용이 검색 조각에 들어 있는 비율. (담당: R3)"""
from experiments._common import NOT_READY_FIXTURE, ROOT, load_fixture
from harness import context


OWNER = "R3"
TITLE = 'RAG k=3 / k=5 → 근거 인용 정확도, 토큰 수'
NOTE = '저장된 인용은 AI가 k=5 검색 결과에서 고른 문장이므로 k=5가 기준선이다. k=5에서도 100%가 아닌 것은 인용이 한 번 더 검색한 결과와 어긋나는 경우가 있어서이며(원인은 따로 조사하지 않았다), k=3은 k=5보다 재현율이 낮고 조각 토큰은 약 22% 적다. 비용을 줄이려고 k를 줄이면 근거 누락이 늘어난다는 뜻이다.'


def run() -> list[dict]:
    import json
    from collections import defaultdict

    from agent.rules import make_classifier
    from mcp_servers.rag.tools import search_disclosure_text

    data = load_fixture()
    if not data:
        return NOT_READY_FIXTURE
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
