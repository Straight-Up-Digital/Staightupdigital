import json

from mcp.server.mcpserver import MCPServer

from researcher import research_website
from auditor import generate_audit
from mcp_auth import run_authenticated_mcp

mcp = MCPServer("Business Opportunity Auditor")


@mcp.tool()
def test_auditor(business_name: str) -> str:
    """Test that the Business Opportunity Auditor is working."""
    return f"Business Opportunity Auditor is working for {business_name}."


@mcp.tool()
def audit_business(website: str) -> str:
    """
    Research a business website and generate a digital presence
    and sales opportunity audit.
    """

    try:
        audit = generate_audit(website)

        return json.dumps(
            audit,
            indent=2,
            ensure_ascii=False
        )

    except Exception as e:
        return json.dumps(
            {
                "success": False,
                "error": str(e)
            },
            indent=2
        )


if __name__ == "__main__":
    run_authenticated_mcp(mcp)