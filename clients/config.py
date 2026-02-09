"""Configuration loading and validation for the LLM client."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class ClientConfig:
    """Immutable configuration for the LLM client."""

    fairsharing_api_key: str
    mcp_server_command: str
    mcp_server_args: list[str]
    provider: str
    model: str
    openai_api_key: str | None
    anthropic_api_key: str | None


def load_config() -> ClientConfig:
    """Load configuration from environment variables and .env file.

    Exits with a clear error message if required variables are missing.
    """
    # Load .env from project root (two levels up from this file, or cwd)
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()  # try cwd

    fairsharing_api_key = os.environ.get("FAIRSHARING_API_KEY", "")
    if not fairsharing_api_key:
        print("Error: FAIRSHARING_API_KEY is required. Set it in .env or environment.", file=sys.stderr)
        sys.exit(1)

    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    model = os.environ.get("LLM_MODEL", "gpt-4o")

    mcp_server_command = os.environ.get("MCP_SERVER_COMMAND", "uv")
    raw_args = os.environ.get("MCP_SERVER_ARGS", "run,fairsharing-mcp")
    mcp_server_args = [a.strip() for a in raw_args.split(",") if a.strip()]

    openai_api_key = os.environ.get("OPENAI_API_KEY")
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")

    # Validate provider-specific keys
    if provider == "openai" and not openai_api_key:
        print("Error: OPENAI_API_KEY is required when LLM_PROVIDER=openai.", file=sys.stderr)
        sys.exit(1)
    elif provider == "anthropic" and not anthropic_api_key:
        print("Error: ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic.", file=sys.stderr)
        sys.exit(1)

    return ClientConfig(
        fairsharing_api_key=fairsharing_api_key,
        mcp_server_command=mcp_server_command,
        mcp_server_args=mcp_server_args,
        provider=provider,
        model=model,
        openai_api_key=openai_api_key,
        anthropic_api_key=anthropic_api_key,
    )
