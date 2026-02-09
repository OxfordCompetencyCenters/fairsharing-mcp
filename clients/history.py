"""Provider-agnostic conversation history model."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Message:
    """A single conversation message."""

    role: str  # "user" or "assistant"
    content: str


@dataclass
class ConversationHistory:
    """Tracks conversation messages in a provider-agnostic format.

    Each provider translates this into its own SDK format internally.
    """

    messages: list[Message] = field(default_factory=list)

    def add_user(self, content: str) -> None:
        self.messages.append(Message(role="user", content=content))

    def add_assistant(self, content: str) -> None:
        self.messages.append(Message(role="assistant", content=content))

    def clear(self) -> None:
        self.messages.clear()
