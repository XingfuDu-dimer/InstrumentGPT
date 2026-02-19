"""
Instrument GPT — Chat-style Q&A powered by the Cursor Agent CLI.

UI layout (ChatGPT-like):
  Left sidebar  — conversation list per IP, new-chat button, settings
  Right main    — current conversation messages with streaming responses

Default working directory for Cursor CLI:
  - Set env INSTRUMENT_CWD at run time to override (e.g. your target repo).
  - Otherwise defaults to this app's directory (ROOT).
"""
import os
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

import db
import cursor_cli

ROOT = Path(__file__).resolve().parent

# Default cwd: env INSTRUMENT_CWD at start, else ROOT
DEFAULT_CWD = os.environ.get("INSTRUMENT_CWD")
if not DEFAULT_CWD or not Path(DEFAULT_CWD).exists():
    DEFAULT_CWD = str(ROOT)

DEFAULT_MODEL = ""
DEFAULT_MODE = "agent"
DEFAULT_MDC_TAG = "@log-download-and-debug.mdc"

db.init_db()

# ── Helpers ──────────────────────────────────────────────────────────────────


def get_client_ip() -> str:
    try:
        ctx = getattr(st, "context", None)
        if ctx is not None:
            ip = getattr(ctx, "ip_address", None)
            if ip:
                return ip
    except Exception:
        pass
    return "127.0.0.1"


def auto_title(question: str) -> str:
    title = question.strip().split("\n")[0]
    return (title[:47] + "...") if len(title) > 50 else (title or "New Chat")


def enrich_prompt(question: str, mdc_tag: str) -> str:
    tag = mdc_tag.strip()
    if not tag or tag in question:
        return question
    return (
        f"Use {tag} as the primary guide for downloading latest logs "
        f"and debugging.\nFollow its instrument/IP mapping and workflow."
        f"\n\n{question}"
    )


def build_context_prompt(messages: list[dict], new_question: str) -> str:
    """Include recent history when we cannot --resume a CLI session."""
    if not messages:
        return new_question
    parts = ["Previous conversation:\n"]
    for msg in messages[-10:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"]
        if len(content) > 2000:
            content = content[:2000] + "…"
        parts.append(f"\n{role}: {content}\n")
    parts.append(f"\n---\nNew question: {new_question}")
    return "\n".join(parts)


def relative_time(ts: float) -> str:
    diff = time.time() - ts
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff / 60)}m ago"
    if diff < 86400:
        return f"{int(diff / 3600)}h ago"
    return datetime.fromtimestamp(ts).strftime("%m/%d")


# ── Page config & CSS ────────────────────────────────────────────────────────

st.set_page_config(page_title="Instrument GPT", page_icon="🔬", layout="wide")

st.markdown(
    """
<style>
/* ---- sidebar ---- */
section[data-testid="stSidebar"] {
    background-color: #171720;
    min-width: 260px;
}
section[data-testid="stSidebar"] .stButton > button {
    width: 100%;
    text-align: left;
    padding: 0.45rem 0.7rem;
    border-radius: 0.5rem;
    border: none;
    background: transparent;
    color: #c9d1d9;
    font-size: 0.84rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    background-color: #2a2a3d;
}
/* ---- main area ---- */
.main .block-container {
    max-width: 840px;
    padding-top: 1.2rem;
}
/* hide chrome */
#MainMenu, footer, header {visibility: hidden;}
/* tool indicator */
.tool-ind {
    font-size: 0.78rem;
    color: #777;
    padding: 1px 0;
    font-family: monospace;
}
</style>
""",
    unsafe_allow_html=True,
)

# ── Session state defaults ───────────────────────────────────────────────────

if "current_conv" not in st.session_state:
    st.session_state.current_conv = None

if "settings" not in st.session_state:
    st.session_state.settings = {
        "model": DEFAULT_MODEL,
        "mode": DEFAULT_MODE,
        "mdc_tag": DEFAULT_MDC_TAG,
        "cwd": DEFAULT_CWD,
    }

client_ip = get_client_ip()

# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### 🔬 Instrument GPT")

    if st.button("＋  New Chat", key="btn_new_chat", use_container_width=True):
        st.session_state.current_conv = None
        st.rerun()

    st.divider()

    conversations = db.get_conversations(client_ip)

    for conv in conversations:
        is_active = st.session_state.current_conv == conv["id"]
        col_title, col_del = st.columns([5, 1])
        with col_title:
            label = ("▸ " if is_active else "") + conv["title"]
            if st.button(
                label,
                key=f"c_{conv['id']}",
                use_container_width=True,
                help=relative_time(conv["updated_at"]),
            ):
                st.session_state.current_conv = conv["id"]
                st.rerun()
        with col_del:
            if st.button("×", key=f"d_{conv['id']}"):
                db.delete_conversation(conv["id"])
                if is_active:
                    st.session_state.current_conv = None
                st.rerun()

    if conversations:
        st.divider()

    with st.expander("⚙  Settings"):
        st.session_state.settings["model"] = st.text_input(
            "Model",
            value=st.session_state.settings["model"],
            placeholder="(default)",
            help="Leave empty for default model",
        )
        st.session_state.settings["mode"] = st.selectbox(
            "Mode",
            ["agent", "ask", "plan"],
            index=["agent", "ask", "plan"].index(
                st.session_state.settings["mode"]
            ),
        )
        st.session_state.settings["mdc_tag"] = st.text_input(
            "MDC Tag",
            value=st.session_state.settings["mdc_tag"],
            help="Prepended to every question for log-download guidance",
        )
        st.session_state.settings["cwd"] = st.text_input(
            "Working Directory",
            value=st.session_state.settings["cwd"],
            help="Cursor CLI cwd (repo to operate on). Default at start: INSTRUMENT_CWD or app dir.",
        )

# ── Main area — load conversation ────────────────────────────────────────────

conv_id = st.session_state.current_conv
conv_info: dict | None = None

if conv_id:
    conv_info = db.get_conversation(conv_id)
    if not conv_info:
        st.session_state.current_conv = None
        st.rerun()
    messages = db.get_messages(conv_id)
else:
    messages = []

# Welcome screen when no conversation selected
if not conv_id:
    st.markdown("## 🔬 Instrument GPT")
    st.markdown(
        "Ask questions about instruments, logs, and debugging.  \n"
        "Start a new conversation or pick one from the sidebar."
    )

# Render existing messages
for msg in messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ── Chat input & streaming response ─────────────────────────────────────────

if prompt := st.chat_input("Ask anything…"):
    settings = st.session_state.settings

    # Create conversation on first message
    if not conv_id:
        conv_id = db.create_conversation(client_ip, auto_title(prompt))
        st.session_state.current_conv = conv_id
        conv_info = db.get_conversation(conv_id)

    # Persist & show the user message
    db.add_message(conv_id, "user", prompt)
    with st.chat_message("user"):
        st.markdown(prompt)

    # Build enriched prompt
    enriched = enrich_prompt(prompt, settings.get("mdc_tag", ""))
    cli_session = conv_info.get("cli_session_id") if conv_info else None

    # If we have no CLI session to resume, prepend conversation history
    if not cli_session and messages:
        enriched = build_context_prompt(messages, enriched)

    # Stream the assistant response
    with st.chat_message("assistant"):
        response_area = st.empty()
        tool_area = st.empty()
        full_response = ""

        for evt_type, payload in cursor_cli.stream_response(
            prompt=enriched,
            cwd=settings.get("cwd"),
            model=settings.get("model") or None,
            mode=settings.get("mode", "agent"),
            resume_session=cli_session,
        ):
            if evt_type == "text":
                full_response += payload
                response_area.markdown(full_response + "▌")

            elif evt_type == "tool":
                tool_area.markdown(
                    f'<p class="tool-ind">🔧 {payload}</p>',
                    unsafe_allow_html=True,
                )

            elif evt_type == "session_id":
                db.update_cli_session(conv_id, payload)

            elif evt_type == "error" and not full_response:
                full_response = f"**Error:** {payload}"

            elif evt_type == "done":
                tool_area.empty()
                response_area.markdown(
                    full_response or "_No response received._"
                )

    # Persist the assistant reply
    if full_response:
        db.add_message(conv_id, "assistant", full_response)

    # Auto-title from first user message
    user_msgs = [m for m in db.get_messages(conv_id) if m["role"] == "user"]
    if len(user_msgs) == 1:
        db.update_title(conv_id, auto_title(prompt))

    st.rerun()
