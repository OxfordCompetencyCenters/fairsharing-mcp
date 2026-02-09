"""Append-only JSONL conversation logger."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)


def _default_log_dir() -> str:
    """Return the default log directory.

    On Azure App Service ``/home/LogFiles`` persists across restarts.
    Locally, fall back to ``logs/conversations`` under the project root.
    """
    if os.path.isdir("/home/LogFiles"):
        return "/home/LogFiles/conversations"
    return str(Path(__file__).resolve().parent.parent / "logs" / "conversations")


class ConversationLogger:
    """Thread-safe, append-only JSONL conversation logger.

    Each instance is bound to a ``session_id`` (auto-generated UUID by
    default).  Log entries are appended to daily files named
    ``conversations-YYYY-MM-DD.jsonl`` inside *log_dir*.

    All I/O errors are caught so logging never breaks the chat.
    """

    def __init__(self, session_id: str | None = None, log_dir: str | None = None) -> None:
        self.session_id = session_id or uuid4().hex
        self.log_dir = Path(log_dir or os.environ.get("CONVERSATION_LOG_DIR", "") or _default_log_dir())
        self._turn = 0
        self._lock = threading.Lock()
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("Could not create conversation log dir: %s", self.log_dir)

    def log_turn(self, user_query: str, assistant_response: str) -> None:
        """Append one conversation turn to today's log file."""
        self._turn += 1
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "turn": self._turn,
            "user_query": user_query,
            "assistant_response": assistant_response,
        }
        try:
            line = json.dumps(entry, ensure_ascii=False)
            path = self.log_dir / f"conversations-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
            with self._lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            logger.warning("Failed to log conversation turn %d", self._turn, exc_info=True)
