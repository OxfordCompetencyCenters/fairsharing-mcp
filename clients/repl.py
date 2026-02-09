"""Interactive REPL for the FAIRsharing LLM client."""

from __future__ import annotations

import asyncio
import sys
from functools import partial

from .config import ClientConfig, load_config
from .history import ConversationHistory
from .providers import create_provider

HELP_TEXT = """\
Commands:
  /help   - Show this help message
  /clear  - Clear conversation history and start fresh
  /quit   - Exit the client

Type any question to query FAIRsharing via the LLM.\
"""


async def run_repl(config: ClientConfig) -> None:
    """Run the interactive REPL loop."""
    history = ConversationHistory()
    provider = create_provider(config)

    print(f"FAIRsharing MCP Client (provider={config.provider}, model={config.model})")
    print("Type /help for commands, /quit to exit.\n")

    loop = asyncio.get_running_loop()

    async with provider:
        while True:
            # Read input without blocking the event loop
            try:
                user_input = await loop.run_in_executor(None, partial(input, "You: "))
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            # Handle commands
            if user_input.lower() == "/quit":
                print("Goodbye!")
                break
            if user_input.lower() == "/help":
                print(HELP_TEXT)
                continue
            if user_input.lower() == "/clear":
                history.clear()
                provider.clear_conversation()
                print("Conversation cleared.\n")
                continue

            # Send to LLM
            history.add_user(user_input)
            try:
                response = await provider.send_message(user_input, history)
            except KeyboardInterrupt:
                print("\n[Interrupted]")
                continue
            except Exception as e:
                print(f"\n[Error: {e}]\n")
                # Remove the failed user message from history
                if history.messages and history.messages[-1].role == "user":
                    history.messages.pop()
                continue

            history.add_assistant(response)
            print(f"\nAssistant: {response}\n")


def main() -> None:
    """Sync entry point."""
    config = load_config()
    try:
        asyncio.run(run_repl(config))
    except KeyboardInterrupt:
        print("\nGoodbye!")
        sys.exit(0)
