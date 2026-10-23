"""rag-mcp 서버 (FastMCP, Streamable HTTP, 포트 8002).

실행: uv run python -m mcp_servers.rag.server   (먼저 `python -m mcp_servers.rag.ingest`로 색인을 만든다)
"""
from fastmcp import FastMCP

from mcp_servers.rag.tools import release_disclosure_index, search_disclosure_text, search_reference

PORT = 8002  # 기본값. RAG_MCP_URL의 포트가 다르면 그 포트로 뜬다

mcp = FastMCP("rag-mcp")
_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
mcp.tool(search_disclosure_text, annotations=_READ_ONLY)
mcp.tool(search_reference, annotations=_READ_ONLY)
# 데이터(색인)를 지우는 도구: 모델이 아니라 Agent 코드만 부른다 (운영 모드에서만 동작)
mcp.tool(release_disclosure_index, annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False})

if __name__ == "__main__":
    from urllib.parse import urlparse

    from shared.config import get_settings

    port = urlparse(get_settings().RAG_MCP_URL).port or PORT
    mcp.run(transport="streamable-http", host="127.0.0.1", port=port, path="/mcp")
