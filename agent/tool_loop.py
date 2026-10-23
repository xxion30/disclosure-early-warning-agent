"""모델이 읽기 전용 도구를 직접 고르는 조회 단계 (최대 MAX_ROUNDS번 반복).

모델은 "무엇을 더 찾아볼지"만 제안하고, 실행·제한·기록은 코드가 맡는다.
- 고를 수 있는 도구는 READ_ONLY_TOOLS 두 개뿐이다. 데이터를 바꾸는 도구는 모델 선택지에 없다.
- search_disclosure_text의 rcept_no는 코드가 현재 공시로 고정한다 (모델이 다른 공시를 읽을 수 없다).
- 반복: 모델이 요청을 내면 실행하고, 그 결과(이력)를 보여 준 뒤 한 번 더 묻는다. 요청이 비거나 같은 요청이 반복되거나
  MAX_ROUNDS에 닿으면 멈춘다. 한 번에 최대 MAX_REQUESTS개만 실행한다.
- 이력이 길어지면 harness.context.trim_messages가 오래된 조회 결과를 요약해 HISTORY_BUDGET 토큰 안으로 줄인다 (Harness 3: Context).
- 모델 호출 횟수(8회)·같은 도구 3회 한도는 execution.py가 센다. 한 노드가 쓸 수 있는 선택 호출은 MAX_ROUNDS번이라
  전체 흐름(선택 2노드 + 추출·설명·초안)이 8회 안에 들어온다.
- 도구 오류는 예외로 멈추지 않고 모델에 돌려줄 관찰 결과("오류: CODE")로 남긴다.
"""
from agent.execution import call_tool, generate
from agent.ports import AgentError
from agent.retrieval import RAGContractError
from agent.state import ToolSelection
from harness import context as ctx

READ_ONLY_TOOLS = ("search_disclosure_text", "search_reference")
MAX_REQUESTS = 2
MAX_ROUNDS = 2
HISTORY_BUDGET = ctx.TOKEN_BUDGET // 3  # 이력에 쓸 수 있는 토큰 (전체 입력 예산 6,000의 1/3)
PREVIEW_CHARS = 300
REFERENCE_CATEGORIES = ("상장규정", "공시해설", "회계용어", "안내문가이드")


def _summarize(messages: list) -> str:
    """오래된 조회 이력을 한 줄씩으로 줄인다 (모델 호출 없이 코드로 요약 — 호출 횟수를 쓰지 않는다)."""
    return "\n".join(str(m["content"]).splitlines()[0][:120] for m in messages)


def _history_for_model(purpose: str, history: list[dict]) -> list[str]:
    msgs = ctx.trim_messages([{"role": "system", "content": purpose}, *history], _summarize, HISTORY_BUDGET)
    return [m["content"] for m in msgs[1:]]


async def select_and_run(deps, purpose: str, rcept_no: str, context: dict) -> dict:
    """모델에게 추가 조회가 필요한지 묻고, 고른 읽기 전용 도구를 실행한다 (최대 MAX_ROUNDS번).

    반환: {"disclosure_chunks": [...], "references": [...], "observations": [...]}
    """
    out: dict = {"disclosure_chunks": [], "references": [], "observations": []}
    history: list[dict] = []
    done: set[tuple] = set()
    for _round in range(MAX_ROUNDS):
        try:
            selection = await generate(deps, "select_tools", ToolSelection, {
                "purpose": purpose, **context, "history": _history_for_model(purpose, history)})
        except ValueError:  # 모델이 형식에 맞지 않는 선택을 냈다 → 추가 조회 없이 지금까지의 결과로 진행
            out["observations"].append({"tool": None, "error": "INVALID_SELECTION"})
            break
        requests = [r for r in selection.requests[:MAX_REQUESTS] if (r.tool, r.query, r.category) not in done]
        if not requests:  # 더 찾을 게 없거나 이미 한 질문을 되풀이한다
            break
        lines: list[str] = []
        for req in requests:
            done.add((req.tool, req.query, req.category))
            if req.tool not in READ_ONLY_TOOLS:  # 스키마에서 걸러지지만 코드에서도 한 번 더 막는다
                raise AgentError(f"모델이 허용되지 않은 도구를 요청: {req.tool}")
            if req.tool == "search_disclosure_text":
                args = {"rcept_no": rcept_no, "query": req.query, "k": 5}
            else:
                if req.category not in REFERENCE_CATEGORIES:
                    out["observations"].append({"tool": req.tool, "error": "INVALID_INPUT"})
                    lines.append(f"{req.tool}({req.query}) → 오류: INVALID_INPUT")
                    continue
                args = {"query": req.query, "category": req.category, "k": 3}
            try:
                data = await call_tool(deps, req.tool, args)
            except AgentError as exc:
                if isinstance(exc, RAGContractError):
                    raise
                if exc.status == "FAILED":
                    raise
                # 도구 실패는 모델에게 돌려줄 관찰로만 남긴다 (기본 조회 결과로 계속 진행)
                out["observations"].append({"tool": req.tool, "error": str(exc)})
                lines.append(f"{req.tool}({req.query}) → 오류")
                continue
            chunks = data.get("chunks") if isinstance(data, dict) else data
            chunks = [c for c in (chunks or []) if isinstance(c, dict)][:5]
            if isinstance(data, dict):
                chunks = [{**c, **{key: data[key] for key in ('content_sha256', 'normalization_version')
                                  if data.get(key)}} for c in chunks]
            if req.tool == "search_disclosure_text":
                # 현재 공시의 조각만 근거 후보로 쓴다
                out["disclosure_chunks"] += [c for c in chunks if c.get("rcept_no") == rcept_no]
            else:
                out["references"] += chunks
            out["observations"].append({"tool": req.tool, "query": req.query, "returned": len(chunks)})
            previews = " / ".join(str(c.get("text", ""))[:PREVIEW_CHARS].replace("\n", " ") for c in chunks[:2])
            lines.append(f"{req.tool}({req.query}) → {len(chunks)}건: {previews}")
        history.append({"role": "user", "content": "\n".join(lines)})
    return out
