"""business-mcp 서버 (FastMCP, Streamable HTTP, 포트 8001).

실행: uv run python -m mcp_servers.business.server
"""
from fastmcp import FastMCP

from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
from mcp_servers.business.tools.create_contact_task import create_contact_task
from mcp_servers.business.tools.get_company_profile import get_company_profile
from mcp_servers.business.tools.get_disclosure import get_disclosure
from mcp_servers.business.tools.get_holders import get_holders
from mcp_servers.business.tools.get_signal_history import get_signal_history
from mcp_servers.business.tools.list_new_disclosures import list_new_disclosures
from mcp_servers.business.tools.purge_disclosure_text import purge_disclosure_text
from mcp_servers.business.tools.save_assessment import save_assessment
from mcp_servers.business.tools.update_task_status import update_task_status

PORT = 8001  # 기본값. BUSINESS_MCP_URL의 포트가 다르면 그 포트로 뜬다 (한 노트북에 시험용 서버를 따로 띄울 때)

mcp = FastMCP("business-mcp")

# MCP 도구 주석(annotations): 조회 도구는 readOnlyHint, 데이터를 바꾸는 도구는 destructiveHint로 표시한다.
# Agent·MCP 클라이언트가 변경 도구를 알아보고 실행 전에 사람의 승인을 거치게 하는 근거다 (수업 자료: 변경 도구는 destructiveHint).
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
MUTATING = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False}

for _tool, _hints in (
    (list_new_disclosures, READ_ONLY),
    (get_disclosure, READ_ONLY),
    (get_company_profile, READ_ONLY),
    (get_signal_history, READ_ONLY),
    (calculate_risk_score, READ_ONLY),  # 규칙 계산이지만 DB를 바꾸지 않는다
    (get_holders, READ_ONLY),
    (save_assessment, MUTATING),  # rcept_no 기준 upsert (같은 입력이면 같은 결과)
    (create_contact_task, MUTATING),  # PB 승인 뒤에만, (평가, 고객)당 1건 (멱등)
    (update_task_status, MUTATING),  # PB가 화면에서만
    (purge_disclosure_text, MUTATING),  # 처리가 끝난 공시의 본문을 비움 (운영 모드에서만 동작, Agent 코드만 호출)
):
    mcp.tool(_tool, annotations=_hints)

if __name__ == "__main__":
    from shared.db import init_db

    init_db()
    from urllib.parse import urlparse

    from shared.config import get_settings

    port = urlparse(get_settings().BUSINESS_MCP_URL).port or PORT
    mcp.run(transport="streamable-http", host="127.0.0.1", port=port, path="/mcp")
