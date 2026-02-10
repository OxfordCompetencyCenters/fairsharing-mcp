"""FAIRsharing MCP Server - Application instance and shared state."""

import asyncio
import atexit
import logging
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from fairsharing_mcp.client import FAIRsharingClient, FAIRsharingError

# CRITICAL: Configure logging to stderr (never use print() for STDIO servers!)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],  # Defaults to stderr
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize FastMCP server
mcp = FastMCP("fairsharing_mcp")

# Initialize client lazily (will be created on first use)
_client: FAIRsharingClient | None = None


def get_client() -> FAIRsharingClient:
    """Get or create the FAIRsharing client."""
    global _client
    if _client is None:
        api_key = os.getenv("FAIRSHARING_API_KEY")
        if not api_key:
            raise FAIRsharingError(
                "FAIRSHARING_API_KEY environment variable is not set. "
                "Please set it to your FAIRsharing API key."
            )
        base_url = os.getenv("FAIRSHARING_API_URL")
        _client = FAIRsharingClient(api_key=api_key, base_url=base_url)
    return _client


def _shutdown_client() -> None:
    """Close the HTTP client on process exit to release connections."""
    global _client
    if _client is not None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_client.aclose())
            else:
                loop.run_until_complete(_client.aclose())
        except Exception:
            pass  # Best-effort cleanup at shutdown
        _client = None


atexit.register(_shutdown_client)
