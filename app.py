"""
Instrument GPT — Chat-style Q&A powered by the Cursor Agent CLI.

UI layout (ChatGPT-like):
  Left sidebar  — conversation list per IP, new-chat button, settings
  Right main    — current conversation messages with streaming responses

Default working directory for Cursor CLI:
  - Set env INSTRUMENT_CWD at run time to override (e.g. your target repo).
  - Otherwise defaults to this app's directory (ROOT).
"""
import html
import os
import time
from datetime import timedelta
from pathlib import Path

import streamlit as st

import cursor_cli
import db
import knowledge
import memory
import prompt_utils
import media_utils
from ui_styles import SIDEBAR_AND_MAIN_CSS

ROOT = Path(__file__).resolve().parent

# Default cwd: env INSTRUMENT_CWD at start, else ROOT
DEFAULT_CWD = os.environ.get("INSTRUMENT_CWD")
if not DEFAULT_CWD or not Path(DEFAULT_CWD).exists():
    DEFAULT_CWD = r"C:\Users\XingfuDu\Desktop\Instrument"

DEFAULT_MODEL = ""
DEFAULT_MODE = "agent"
DEFAULT_MDC_TAG = "@log-download-and-debug.mdc"

_MODEL_OPTIONS = [
    "",
    "Composer 1.5",
    "Claude 4.6 Sonnet",
    "Claude 4.6 Opus",
    "GPT-5.2",
    "GPT-5.3 Codex",
    "Gemini 3.1 Pro",
    "Gemini 3 Flash",
    "Grok Code",
    "Claude 4.5 Sonnet",
    "Claude 4.5 Opus",
    "Composer 1",
]
_MODEL_LABELS = {
    "": "Auto (default)",
    "Composer 1.5": "Composer 1.5",
    "Claude 4.6 Sonnet": "Claude 4.6 Sonnet",
    "Claude 4.6 Opus": "Claude 4.6 Opus",
    "GPT-5.2": "GPT-5.2",
    "GPT-5.3 Codex": "GPT-5.3 Codex",
    "Gemini 3.1 Pro": "Gemini 3.1 Pro",
    "Gemini 3 Flash": "Gemini 3 Flash",
    "Grok Code": "Grok Code  —  xAI, $0.2 in",
    "Claude 4.5 Sonnet": "Claude 4.5 Sonnet",
    "Claude 4.5 Opus": "Claude 4.5 Opus",
    "Composer 1": "Composer 1",
}

db.init_db()


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


# ── Clean up interrupted streaming (e.g. page refresh during stream) ───────────
# With sync streaming we rarely hit this; handles stale state from refresh

if "_streaming_proc" in st.session_state:
    proc = st.session_state.pop("_streaming_proc")
    cursor_cli.kill_process(proc)

    partial = st.session_state.pop("_partial_response", "")
    cid = st.session_state.pop("_streaming_conv_id", None)
    if partial and cid:
        db.add_message(cid, "assistant", partial + "\n\n*(generation stopped)*")

    title_prompt = st.session_state.pop("_streaming_auto_title_prompt", None)
    if title_prompt and cid:
        user_msgs = [m for m in db.get_messages(cid) if m["role"] == "user"]
        if len(user_msgs) == 1:
            db.update_title(cid, prompt_utils.auto_title(title_prompt))


# ── Page config & CSS ────────────────────────────────────────────────────────

st.set_page_config(page_title="Instrument GPT", page_icon="🔬", layout="wide")
st.markdown(SIDEBAR_AND_MAIN_CSS, unsafe_allow_html=True)

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
                help=media_utils.relative_time(conv["updated_at"]),
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

    with st.expander("📖  How to Use"):
        st.markdown("""
**Instrument GPT** helps you download instrument logs, analyze errors, plot data, and debug with your codebase — all through natural conversation.

---

#### Supported Devices

| Device | IP |
|--------|-----|
| zspr 050 | 10.1.1.45 |
| zspr 051 | 10.1.1.46 |
| zspr 052 | 10.1.1.47 |
| zspr 053 | 10.1.1.48 |
| zspr 054 | 10.1.1.49 |
| zspr 055 | 10.1.1.50 |

You can refer to a device by IP (e.g. `10.1.1.47`).

---

#### Quick Start — Example Prompts

**Analyze an error** (specify device + describe the problem):
> `10.1.1.47 Door open timeout error, what happened?`

> `10.1.1.45 LED not blinking, can you check the logs?`

**Check a specific log session**:
> `10.1.1.47 check InstrumentDebug_2026-02-13_00-44-28.1.log for temp drop`

**Paste log content for analysis** (include device):
> `10.1.1.47 [2026-02-13 05:04:52.782][debug] temp 61.9, next line temp 29.4 — why the sudden drop?`

**Plot PID / temperature control data**:
> `10.1.1.47 plot temp control`

> `10.1.1.45 plot PID`

**Download all logs from a device**:
> `10.1.1.47 download all logs`

**General questions (no device needed)**:
> `What causes a Door timeout error?`

> `How does the DoorController handle initialization?`

---

#### Tips
- **Include the device IP** in your question to trigger automatic log download and analysis.
- **Without an IP**, the assistant answers from general knowledge and the codebase only (no download).
- After downloading, the assistant analyzes the logs, cross-references your source code, and reports root cause, timeline, and fix suggestions.
- **Interactive charts**: When you ask to plot data, the chart supports zoom, pan, and hover — use your mouse to explore.
""")

    with st.expander("⚙  Settings"):
        current_model = st.session_state.settings["model"]
        if current_model not in _MODEL_OPTIONS:
            _MODEL_OPTIONS.append(current_model)
            _MODEL_LABELS[current_model] = current_model
        st.session_state.settings["model"] = st.selectbox(
            "Model",
            options=_MODEL_OPTIONS,
            index=_MODEL_OPTIONS.index(current_model),
            format_func=lambda m: _MODEL_LABELS.get(m, m),
            help="Select the model for the Cursor Agent CLI",
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
    safe_ip = html.escape(client_ip)
    st.markdown(
        f'<div class="welcome-card">'
        f'<p class="greeting">Hello User,   <span class="ip">{safe_ip}</span></p>'
        f'<p class="sub">Ask questions about instruments, logs, and debugging.<br>'
        f'Start a new conversation or pick one from the sidebar.</p>'
        f'</div>',
        unsafe_allow_html=True,
    )

# Render existing messages with per-answer like
# Use fragment to auto-refresh like status when summarization is in progress
cwd = st.session_state.settings.get("cwd", "")
_has_pending = False
if conv_id:
    _liked = db.get_liked_entries_for_conversation(conv_id)
    _has_pending = any(e.get("status") in ("pending", "summarizing") for e in _liked.values())


@st.fragment(run_every=timedelta(seconds=2) if _has_pending else None)
def _messages_with_likes():
    liked = db.get_liked_entries_for_conversation(conv_id) if conv_id else {}
    for msg in messages:
        with st.chat_message(msg["role"]):
            media_utils.render_message(msg["content"])
            if conv_id and msg["role"] == "assistant" and "id" in msg:
                mid = msg["id"]
                entry = liked.get(mid)
                if not entry:
                    if st.button("👍", key=f"like_{mid}", help="Add to knowledge base", type="secondary"):
                        ok, m = knowledge.start_summarization(conv_id, mid, cwd)
                        st.toast(m)
                        st.rerun()
                elif entry["status"] in ("pending", "summarizing"):
                    if st.button("👍 … ✕", key=f"cancel_{mid}", help="Cancel summarization"):
                        ok, m = knowledge.cancel_or_unlike(conv_id, mid)
                        st.toast(m)
                        st.rerun()
                else:
                    if st.button("✓", key=f"unlike_{mid}", help="In base · click to remove", type="secondary"):
                        ok, m = knowledge.cancel_or_unlike(conv_id, mid)
                        st.toast(m)
                        st.rerun()


_messages_with_likes()

# ── Chat input & streaming response ─────────────────────────────────────────

if prompt := st.chat_input("Ask anything…"):
    settings = st.session_state.settings

    # Create conversation on first message
    if not conv_id:
        conv_id = db.create_conversation(client_ip, prompt_utils.auto_title(prompt))
        st.session_state.current_conv = conv_id
        conv_info = db.get_conversation(conv_id)

    # Persist & show the user message
    db.add_message(conv_id, "user", prompt)
    with st.chat_message("user"):
        st.markdown(prompt)

    # Build enriched prompt
    enriched = prompt_utils.enrich_prompt(prompt, settings.get("mdc_tag", ""))
    cli_session = conv_info.get("cli_session_id") if conv_info else None

    # Load memory and build structured context
    existing_summary, state_json = db.get_memory(conv_id)
    diag_state = memory.DiagnosticState.deserialize(state_json)

    ip_result = prompt_utils.extract_ip(prompt)
    if ip_result:
        diag_state.device_ip = ip_result[0]
        diag_state.device_name = f"zspr {ip_result[1]}"

    if messages:
        enriched, updated_summary = memory.build_prompt(
            current_question=enriched,
            all_messages=messages,
            diagnostic_state=diag_state,
            existing_summary=existing_summary,
            is_device_query=prompt_utils.has_device(prompt),
        )
    else:
        updated_summary = existing_summary

    # Debug: show the actual prompt sent to CLI
    with st.expander("Debug: actual prompt sent", expanded=False):
        st.code(enriched, language="markdown")

    request_start_time = time.time()

    # Start the CLI process
    process, proc_err = cursor_cli.create_process(
        prompt=enriched,
        cwd=settings.get("cwd"),
        model=settings.get("model") or None,
        mode=settings.get("mode", "agent"),
        resume_session=cli_session,
    )

    if proc_err:
        with st.chat_message("assistant"):
            st.markdown(f"**Error:** {proc_err}")
            db.add_message(conv_id, "assistant", f"**Error:** {proc_err}")
        st.rerun()

    # Stream in main thread (sync)
    st.session_state._streaming_proc = process
    st.session_state._streaming_conv_id = conv_id
    st.session_state._partial_response = ""
    st.session_state._streaming_auto_title_prompt = prompt

    with st.chat_message("assistant"):
        response_area = st.empty()
        tool_area = st.empty()
        stop_area = st.empty()
        full_response = ""

        stop_area.button("⏹ Stop", key="stop_gen", type="secondary")

        for evt_type, payload in cursor_cli.iter_events(process):
            if evt_type == "text":
                full_response += payload
                st.session_state._partial_response = full_response
                response_area.markdown(full_response + "▌")

            elif evt_type == "text_replace":
                full_response = payload
                st.session_state._partial_response = full_response
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
                stop_area.empty()
                response_area.markdown(full_response or "_No response received._")

        # Plotly/images
        plotly_cache, plotly_fig = media_utils.try_interactive_plot(
            settings.get("cwd", ""), full_response,
        )
        if plotly_fig:
            st.plotly_chart(plotly_fig, use_container_width=True, key=f"plotly_live_{conv_id}")
            full_response = media_utils.attach_plotly(full_response, plotly_cache)
        else:
            new_images = media_utils.find_new_images(
                settings.get("cwd", ""), request_start_time, full_response,
            )
            for img_path in new_images:
                st.image(img_path, caption=os.path.basename(img_path))
            if new_images:
                full_response = media_utils.attach_images(full_response, new_images)

    # Clear streaming state
    st.session_state.pop("_streaming_proc", None)
    st.session_state.pop("_streaming_conv_id", None)
    st.session_state.pop("_partial_response", None)
    st.session_state.pop("_streaming_auto_title_prompt", None)

    if full_response:
        db.add_message(conv_id, "assistant", full_response)

    diag_state = memory.extract_state_updates(full_response, diag_state)
    db.update_memory(conv_id, updated_summary, diag_state.serialize())

    user_msgs = [m for m in db.get_messages(conv_id) if m["role"] == "user"]
    if len(user_msgs) == 1:
        db.update_title(conv_id, prompt_utils.auto_title(prompt))

    st.rerun()
