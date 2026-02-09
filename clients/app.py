"""Streamlit chat UI for the FAIRsharing MCP client."""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import streamlit as st

# Ensure project root is on sys.path so `clients.*` imports resolve
# when running via `streamlit run clients/app.py`.
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from clients.config import load_config  # noqa: E402
from clients.conversation_logger import ConversationLogger  # noqa: E402
from clients.history import ConversationHistory  # noqa: E402
from clients.providers import create_provider  # noqa: E402

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="FAIRsharing Assistant",
    page_icon="\U0001f9ec",  # DNA emoji
    layout="wide",
)

# ---------------------------------------------------------------------------
# Async infrastructure — a single background event-loop shared for the session
# ---------------------------------------------------------------------------

def _get_loop() -> asyncio.AbstractEventLoop:
    """Return the persistent background event loop, creating it on first call."""
    if "event_loop" not in st.session_state:
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        st.session_state.event_loop = loop
    return st.session_state.event_loop


def _run_async(coro):
    """Submit *coro* to the background loop and block until it finishes."""
    future = asyncio.run_coroutine_threadsafe(coro, _get_loop())
    return future.result(timeout=300)  # 5-minute ceiling


# ---------------------------------------------------------------------------
# Provider lifecycle
# ---------------------------------------------------------------------------

def _init_provider() -> None:
    """Initialise the LLM provider + MCP server once per Streamlit session."""
    if "provider" in st.session_state:
        return

    with st.spinner("Starting MCP server and connecting to LLM provider..."):
        config = load_config()
        provider = create_provider(config)
        _run_async(provider.setup())
        st.session_state.provider = provider
        st.session_state.history = ConversationHistory()
        st.session_state.model_name = config.model
        st.session_state.conversation_logger = ConversationLogger(
            log_dir=config.conversation_log_dir or None,
        )


# ---------------------------------------------------------------------------
# Header bar (Clear button top-right)
# ---------------------------------------------------------------------------

def _render_header() -> None:
    col_title, col_btn = st.columns([8, 1])
    with col_title:
        st.title("FAIRsharing Assistant")
    with col_btn:
        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
        if st.button("Clear", type="secondary"):
            st.session_state.messages = []
            st.session_state.history = ConversationHistory()
            if "provider" in st.session_state:
                st.session_state.provider.clear_conversation()
            st.rerun()


# ---------------------------------------------------------------------------
# Chat interface
# ---------------------------------------------------------------------------

def _render_chat() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Replay history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Accept new input
    if prompt := st.chat_input("Ask about FAIRsharing standards, databases, or policies..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    response = _run_async(
                        st.session_state.provider.send_message(
                            prompt, st.session_state.history
                        )
                    )
                except Exception as exc:
                    response = f"Error: {exc}"

            st.markdown(response)

        st.session_state.history.add_user(prompt)
        st.session_state.history.add_assistant(response)
        st.session_state.messages.append({"role": "assistant", "content": response})

        if "conversation_logger" in st.session_state:
            st.session_state.conversation_logger.log_turn(prompt, response)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Hide the sidebar entirely
st.markdown(
    "<style>[data-testid='stSidebar']{display:none}</style>",
    unsafe_allow_html=True,
)

_init_provider()
_render_header()
_render_chat()
