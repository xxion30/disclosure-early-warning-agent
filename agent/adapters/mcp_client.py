"""MCP transport only: write permissions stay in graph code and API adapters."""
import json
from typing import Any
from uuid import uuid4

from shared.schemas import ToolResponse

ALLOWED_TOOLS = frozenset({
    "list_new_disclosures", "get_disclosure", "get_company_profile", "get_signal_history",
    "calculate_risk_score", "get_holders", "save_assessment", "get_risk_state", "save_risk_state",
    "create_contact_task", "search_disclosure_text", "search_reference",
    # 처리가 끝난 공시의 색인·본문 정리 (운영 모드 전용). 코드(assembly.AssessmentStore)만 부르고 모델 선택지(tool_loop)에는 없다.
    "release_disclosure_index", "purge_disclosure_text",
})


def normalize_response(value: Any) -> ToolResponse:
    if isinstance(value, ToolResponse):
        return value
    if getattr(value, "status", None) == "error" or getattr(value, "isError", False):
        raise ValueError("MCP 전송·실행 오류")
    if hasattr(value, "artifact"):
        artifact = value.artifact
        if isinstance(artifact, dict) and artifact.get("structured_content") is not None:
            value = artifact["structured_content"]
        else:
            value = value.content
    if hasattr(value, "structuredContent") and value.structuredContent:
        value = value.structuredContent
    elif hasattr(value, "content"):
        value = value.content
    if isinstance(value, dict) and "structured_content" in value:
        value = value["structured_content"]
    if isinstance(value, list):
        texts = [block["text"] if isinstance(block, dict) else block.text
                 for block in value if (block.get("type") if isinstance(block, dict)
                                        else getattr(block, "type", None)) == "text"]
        if len(texts) != 1:
            raise ValueError("MCP 응답에 단일 JSON 결과가 필요합니다")
        value = texts[0]
    if isinstance(value, str):
        value = json.loads(value)
    result = ToolResponse.model_validate(value)
    if (result.ok and result.error is not None) or (not result.ok and result.error is None):
        raise ValueError("MCP ok/error 형식 불일치")
    return result


class MCPTools:
    def __init__(self, tools: dict):
        self._tools = tools

    @classmethod
    async def connect(cls, settings=None):
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
        except ImportError as exc:
            raise RuntimeError("MCP 의존성 호환 오류: docs/contracts/agent.md 확인") from exc
        from shared.config import get_settings
        settings = settings or get_settings()
        client = MultiServerMCPClient({
            "business": {"url": settings.BUSINESS_MCP_URL, "transport": "streamable_http"},
            "rag": {"url": settings.RAG_MCP_URL, "transport": "streamable_http"},
        })
        discovered = await client.get_tools()
        names = [tool.name for tool in discovered]
        if len(names) != len(set(names)):
            raise ValueError("중복 MCP 도구 이름")
        return cls({tool.name: tool for tool in discovered if tool.name in ALLOWED_TOOLS})

    async def call(self, name: str, arguments: dict) -> ToolResponse:
        if name not in ALLOWED_TOOLS or name not in self._tools:
            raise ValueError(f"사용할 수 없는 MCP 도구: {name}")
        return normalize_response(await self._tools[name].ainvoke({
            "type": "tool_call", "id": str(uuid4()), "name": name, "args": arguments,
        }))
