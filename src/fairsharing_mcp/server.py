"""FAIRsharing MCP Server entry point."""

import logging
import os

import fairsharing_mcp.tools  # noqa: F401 – triggers tool registration
from fairsharing_mcp.app import mcp

logger = logging.getLogger(__name__)


def main():
    """Run the FAIRsharing MCP server.

    Transport is controlled by MCP_TRANSPORT env var:
      - "stdio" (default) — local subprocess, used by Claude Desktop and Streamlit client
      - "streamable-http" — remote HTTP endpoint, used by MCP clients via URL
    """
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    logger.info("Starting FAIRsharing MCP server (transport=%s)...", transport)
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
