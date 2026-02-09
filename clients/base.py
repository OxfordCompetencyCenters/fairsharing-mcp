"""Abstract base class for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Self

from .config import ClientConfig
from .history import ConversationHistory


class BaseLLMProvider(ABC):
    """Base class that all LLM providers must implement."""

    def __init__(self, config: ClientConfig) -> None:
        self.config = config

    @abstractmethod
    async def setup(self) -> None:
        """Start the MCP server subprocess and initialize the LLM connection."""

    @abstractmethod
    async def send_message(self, user_input: str, history: ConversationHistory) -> str:
        """Send a user message and return the assistant's text response."""

    def clear_conversation(self) -> None:
        """Reset any provider-internal conversation state."""

    @abstractmethod
    async def teardown(self) -> None:
        """Clean up resources (MCP subprocess, connections)."""

    async def __aenter__(self) -> Self:
        await self.setup()
        return self

    async def __aexit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: object) -> None:
        await self.teardown()
