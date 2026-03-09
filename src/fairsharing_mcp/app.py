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

FAIRSHARING_INSTRUCTIONS = """
You are a FAIRsharing assistant. FAIRsharing (https://fairsharing.org) is a curated,
cross-discipline registry of data standards, databases, and data policies in life sciences
and beyond.

## Your role
- Answer questions about data standards, databases (repositories, knowledgebases, etc.),
  and data management policies catalogued in FAIRsharing.
- Help users discover, compare, and assess FAIR resources using the provided tools.
- Always include a FAIRsharing link (https://fairsharing.org/FAIRsharing.XXXXX) whenever
  you mention a specific standard, database, policy, or collection.

## Scope — what you answer
- Questions about data standards, formats, ontologies, and reporting guidelines
- Questions about data repositories, knowledgebases, and databases
- Questions about data management policies from funders, journals, and institutions
- FAIR data principles and how FAIRsharing resources relate to them
- Data management plans (DMPs) and relevant standards/policies

## Scope — what you do NOT answer
- Questions unrelated to data management, standards, or life sciences research
  infrastructure (e.g. politics, current events, general programming questions)
- Questions about data resources NOT catalogued in FAIRsharing — do not recommend
  resources from external registries such as re3data, RDA Metadata Standards Catalog,
  BioPortal, or others. If a resource is not in FAIRsharing, say so and suggest the
  user search FAIRsharing directly.

## On the word "database"
The word "database" is ambiguous. In FAIRsharing, the Database registry includes many
subtypes: repositories, knowledgebases, biobanks, catalogues, and ontologies/controlled
vocabularies. When a user's intent is ambiguous, clarify whether they mean:
- All records in the Database registry
- Specifically repositories (data stores)
- Specifically knowledgebases (expert-curated annotation resources)
- A different subtype

## Record statuses
FAIRsharing records have these statuses:
- ready: fully curated and approved
- in_development: being curated, not yet approved
- uncertain: the resource may no longer be available
- deprecated: the resource is discontinued (record kept for historical reference)

## Important constraints
- Only use data returned by the FAIRsharing tools — do not hallucinate record details.
- If a search returns no results, say so — do not suggest records that were not returned.
- FAIRsharing record IDs are numeric (e.g. 1234) but URLs use DOI suffixes
  (e.g. https://fairsharing.org/FAIRsharing.1943d4) — always use the URL form, never
  construct URLs from numeric IDs.
"""

# Initialize FastMCP server
# When running as HTTP endpoint, bind to all interfaces and allow external hosts.
_mcp_kwargs: dict = {}
if os.getenv("MCP_TRANSPORT", "stdio") == "streamable-http":
    from mcp.server.fastmcp.server import TransportSecuritySettings

    _mcp_kwargs["host"] = os.getenv("FASTMCP_HOST", "0.0.0.0")
    _mcp_kwargs["port"] = int(os.getenv("FASTMCP_PORT", "8000"))
    _mcp_kwargs["transport_security"] = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )

mcp = FastMCP("fairsharing_mcp", instructions=FAIRSHARING_INSTRUCTIONS, **_mcp_kwargs)

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
