"""Provider factory for LLM backends."""

from __future__ import annotations

from ..base import BaseLLMProvider
from ..config import ClientConfig


def create_provider(config: ClientConfig) -> BaseLLMProvider:
    """Create an LLM provider instance based on config.provider."""
    if config.provider == "openai":
        from .openai_agents import OpenAIAgentsProvider

        return OpenAIAgentsProvider(config)
    elif config.provider == "anthropic":
        raise NotImplementedError(
            "Anthropic provider is not yet implemented. "
            "Set LLM_PROVIDER=openai or contribute an implementation in "
            "clients/providers/anthropic_claude.py"
        )
    else:
        raise ValueError(f"Unknown LLM provider: {config.provider!r}. Supported: openai")
