"""business-mcp 서버 (FastMCP, Streamable HTTP, 포트 8001).

실행: uv run python -m mcp_servers.business.server
"""
from fastmcp import FastMCP

from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
from mcp_servers.business.tools.compute_customer_impact import compute_customer_impact
from mcp_servers.business.tools.create_contact_task import create_contact_task
from mcp_servers.business.tools.get_company_profile import get_company_profile
from mcp_servers.business.tools.get_disclosure import get_disclosure
from mcp_servers.business.tools.get_holders import get_holders
from mcp_servers.business.tools.get_signal_history import get_signal_history
from mcp_servers.business.tools.list_new_disclosures import list_new_disclosures
from mcp_servers.business.tools.save_assessment import save_assessment
from mcp_servers.business.tools.update_task_status import update_task_status

PORT = 8001

mcp = FastMCP("business-mcp")

for _tool in (
    list_new_disclosures,
    get_disclosure,
    get_company_profile,
    get_signal_history,
    calculate_risk_score,
    get_holders,
    compute_customer_impact,
    save_assessment,
    create_contact_task,
    update_task_status,
):
    mcp.tool(_tool)

if __name__ == "__main__":
    from shared.db import init_db

    init_db()
    mcp.run(transport="streamable-http", host="127.0.0.1", port=PORT, path="/mcp")
