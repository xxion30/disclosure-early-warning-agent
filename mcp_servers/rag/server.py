"""rag-mcp 서버 (FastMCP, Streamable HTTP, 포트 8002).

실행: uv run python -m mcp_servers.rag.server   (먼저 `python -m mcp_servers.rag.ingest`로 색인을 만든다)
"""
from fastmcp import FastMCP

from mcp_servers.rag.tools import search_disclosure_text, search_reference

PORT = 8002

mcp = FastMCP("rag-mcp")
mcp.tool(search_disclosure_text)
mcp.tool(search_reference)

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=PORT, path="/mcp")
