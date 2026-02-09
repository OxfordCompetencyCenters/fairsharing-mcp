"""FAIRsharing MCP Server entry point."""

import logging

import fairsharing_mcp.tools  # noqa: F401 – triggers tool registration
from fairsharing_mcp.app import mcp

logger = logging.getLogger(__name__)


def main():
    """Run the FAIRsharing MCP server."""
    logger.info("Starting FAIRsharing MCP server...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
