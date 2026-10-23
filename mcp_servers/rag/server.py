"""rag-mcp 서버 (FastMCP, Streamable HTTP, 포트 8002).

실행: uv run python -m mcp_servers.rag.server
  - 운영(RAG_INDEX_MODE=ephemeral): 시작할 때 참고 문서를 메모리에 올리고, 공시 원문은 처음 검색할 때 DART에서 받아 메모리에만 색인한다.
  - 데모·평가(persistent, 기본): 먼저 `python -m mcp_servers.rag.ingest`로 Chroma 색인을 만든다 (옛 방식, 마지막 정리 PR에서 없어진다).

FastMCP는 동기 도구를 이벤트 루프 스레드에서 그대로 실행하므로, DART 다운로드·임베딩 같은 긴 작업이 서버 전체를 막는다.
그래서 도구 함수는 동기로 두고(다른 코드·테스트가 직접 부른다) 서버에 등록할 때만 스레드로 넘기는 래퍼를 쓴다.
"""
import asyncio
import functools

from fastmcp import FastMCP

from mcp_servers.rag.tools import release_disclosure_index, search_disclosure_text, search_reference

PORT = 8002  # 기본값. RAG_MCP_URL의 포트가 다르면 그 포트로 뜬다


def threaded(fn):
    """동기 도구를 스레드에서 돌리는 비동기 래퍼 (이름·시그니처·설명은 그대로)."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    return wrapper


mcp = FastMCP("rag-mcp")
_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
mcp.tool(threaded(search_disclosure_text), annotations=_READ_ONLY)
mcp.tool(threaded(search_reference), annotations=_READ_ONLY)
# 데이터(색인)를 지우는 도구: 모델이 아니라 Agent 코드만 부른다 (운영 모드에서만 동작)
mcp.tool(threaded(release_disclosure_index), annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False})

if __name__ == "__main__":
    from urllib.parse import urlparse

    from mcp_servers.rag import filing, reference
    from shared.config import get_settings

    if filing.is_ephemeral():
        reference.warm()  # 첫 요청이 느려지지 않게 미리 (실패해도 시작은 한다)
    port = urlparse(get_settings().RAG_MCP_URL).port or PORT
    mcp.run(transport="streamable-http", host="127.0.0.1", port=port, path="/mcp")
