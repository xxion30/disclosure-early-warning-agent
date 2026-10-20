"""모델이 읽기 전용 도구를 직접 고르는 조회 단계.

모델은 "무엇을 더 찾아볼지"만 제안하고, 실행·제한·기록은 코드가 맡는다.
- 고를 수 있는 도구는 READ_ONLY_TOOLS 두 개뿐이다. 데이터를 바꾸는 도구는 모델 선택지에 없다.
- search_disclosure_text의 rcept_no는 코드가 현재 공시로 고정한다 (모델이 다른 공시를 읽을 수 없다).
- 한 번의 선택에서 최대 MAX_REQUESTS개만 실행한다. 모델 호출 횟수·같은 도구 3회 한도는 execution.py가 센다.
- 도구 오류는 예외로 멈추지 않고 모델에 돌려줄 관찰 결과("오류: CODE")로 남긴다.
"""
from agent.execution import call_tool, generate
from agent.ports import AgentError
from agent.state import ToolSelection

READ_ONLY_TOOLS = ("search_disclosure_text", "search_reference")
MAX_REQUESTS = 2
REFERENCE_CATEGORIES = ("상장규정", "공시해설", "회계용어", "안내문가이드")


async def select_and_run(deps, purpose: str, rcept_no: str, context: dict) -> dict:
    """모델에게 추가 조회가 필요한지 묻고, 고른 읽기 전용 도구를 실행한다.

    반환: {"disclosure_chunks": [...], "references": [...], "observations": [...]}
    """
    out: dict = {"disclosure_chunks": [], "references": [], "observations": []}
    try:
        selection = await generate(deps, "select_tools", ToolSelection, {"purpose": purpose, **context})
    except ValueError:  # 모델이 형식에 맞지 않는 선택을 냈다 → 추가 조회 없이 기본 조회 결과로 진행
        out["observations"].append({"tool": None, "error": "INVALID_SELECTION"})
        return out
    for req in selection.requests[:MAX_REQUESTS]:
        if req.tool not in READ_ONLY_TOOLS:  # 스키마에서 걸러지지만 코드에서도 한 번 더 막는다
            raise AgentError(f"모델이 허용되지 않은 도구를 요청: {req.tool}")
        if req.tool == "search_disclosure_text":
            args = {"rcept_no": rcept_no, "query": req.query, "k": 5}
        else:
            if req.category not in REFERENCE_CATEGORIES:
                out["observations"].append({"tool": req.tool, "error": "INVALID_INPUT"})
                continue
            args = {"query": req.query, "category": req.category, "k": 3}
        try:
            data = await call_tool(deps, req.tool, args)
        except AgentError as exc:
            if exc.status == "FAILED":
                raise
            # 도구 실패는 모델에게 돌려줄 관찰로만 남긴다 (기본 조회 결과로 계속 진행)
            out["observations"].append({"tool": req.tool, "error": str(exc)})
            continue
        chunks = data.get("chunks") if isinstance(data, dict) else data
        chunks = [c for c in (chunks or []) if isinstance(c, dict)][:5]
        if req.tool == "search_disclosure_text":
            # 현재 공시의 조각만 근거 후보로 쓴다
            out["disclosure_chunks"] += [c for c in chunks if c.get("rcept_no") == rcept_no]
        else:
            out["references"] += chunks
        out["observations"].append({"tool": req.tool, "query": req.query, "returned": len(chunks)})
    return out
