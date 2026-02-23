"""
Instrument GPT — Chat-style Q&A powered by the Cursor Agent CLI.

UI layout (ChatGPT-like):
  Left sidebar  — conversation list per IP, new-chat button, settings
  Right main    — current conversation messages with streaming responses

Default working directory for Cursor CLI:
  - Set env INSTRUMENT_CWD at run time to override (e.g. your target repo).
  - Otherwise defaults to this app's directory (ROOT).
"""
import glob
import html
import json
import os
import re
import subprocess
import sys
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


_IP_PATTERN = re.compile(r"10\.1\.1\.(4[5-9]|50)(?!\d)")

_OCTET_TO_DEV = {
    "45": "050", "46": "051", "47": "052",
    "48": "053", "49": "054", "50": "055",
}


def _extract_ip(question: str) -> tuple[str, str] | None:
    """Match 10.1.1.xx in the current message only. Returns (ip, device) or None."""
    m = _IP_PATTERN.search(question)
    if not m:
        return None
    octet = m.group(1)
    dev = _OCTET_TO_DEV.get(octet, f"0{octet}")
    return f"10.1.1.{octet}", dev


def _has_device(question: str) -> bool:
    return _extract_ip(question) is not None


def enrich_prompt(question: str, mdc_tag: str) -> str:
    tag = mdc_tag.strip()
    if not tag or tag in question:
        return question
    result = _extract_ip(question)
    if not result:
        return question
    ip, dev = result
    return (
        f"Use {tag} as the primary guide. "
        f"The user's target device is zspr {dev} ({ip}). "
        f"Proceed directly with their request — do not ask for the device again.\n\n"
        f"{question}"
    )


def build_context_prompt(
    messages: list[dict],
    new_question: str,
    raw_user_input: str = "",
) -> str:
    """Include recent history when we cannot --resume a CLI session.

    The user's actual question comes FIRST so the agent sees it immediately.
    History is appended inside <conversation_history> tags as reference only.
    When the current question is NOT about device debugging, an explicit note
    tells the agent not to download logs even if the history is full of log analysis.
    """
    if not messages:
        return new_question

    is_debug = _has_device(raw_user_input or new_question)

    MAX_HISTORY_CHARS = 50000

    history_parts = []
    total_chars = 0
    for msg in reversed(messages):
        role = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"]
        entry = f"{role}: {content}"
        if total_chars + len(entry) > MAX_HISTORY_CHARS:
            remaining = MAX_HISTORY_CHARS - total_chars
            if remaining > 200:
                history_parts.append(f"{role}: {content[:remaining]}…")
            break
        history_parts.append(entry)
        total_chars += len(entry)
    history_parts.reverse()
    history_block = "\n\n".join(history_parts)

    if is_debug:
        note = (
            "Answer the question above, using history for context. "
            "If logs were already downloaded and analyzed in the history, "
            "reuse those results — do NOT re-download unless the user "
            "explicitly asks for fresh/new logs."
        )
    else:
        note = (
            "Answer the question above directly. "
            "The history may contain log analysis or device debugging, but the "
            "current question is NOT about that — do not download logs or "
            "analyze devices. Just answer the question."
        )

    return (
        f"{new_question}\n\n"
        f"<conversation_history>\n"
        f"{note}\n\n"
        f"{history_block}\n"
        f"</conversation_history>"
    )


_IMAGE_MARKER = "<!-- ATTACHED_IMAGES:"
_IMAGE_EXT_RE = re.compile(r'[\w.\-]+\.(?:png|jpg|jpeg|svg)', re.IGNORECASE)


def _find_new_images(cwd: str, since: float, response_text: str) -> list[str]:
    """Find images created during this request via timestamp scan + response parsing."""
    found: list[str] = []
    if not cwd or not os.path.isdir(cwd):
        return found
    seen: set[str] = set()

    for ext in ("*.png", "*.jpg", "*.jpeg", "*.svg"):
        for p in glob.glob(os.path.join(cwd, "**", ext), recursive=True):
            ap = os.path.abspath(p)
            if ap not in seen and os.path.getmtime(p) > since:
                seen.add(ap)
                found.append(ap)

    for name in _IMAGE_EXT_RE.findall(response_text):
        for p in glob.glob(os.path.join(cwd, "**", name), recursive=True):
            ap = os.path.abspath(p)
            if ap not in seen:
                seen.add(ap)
                found.append(ap)

    found.sort(key=lambda p: os.path.getmtime(p))
    return found


def _attach_images(content: str, image_paths: list[str]) -> str:
    if not image_paths:
        return content
    return f"{content}\n{_IMAGE_MARKER}{'|'.join(image_paths)} -->"


def _split_images(content: str) -> tuple[str, list[str]]:
    if _IMAGE_MARKER not in content:
        return content, []
    idx = content.index(_IMAGE_MARKER)
    text = content[:idx].rstrip()
    marker = content[idx:]
    paths_str = marker[len(_IMAGE_MARKER):-len(" -->")].strip()
    return text, paths_str.split("|") if paths_str else []


_PLOTLY_MARKER = "<!-- PLOTLY_CHART:"


def _try_interactive_plot(cwd: str, response_text: str):
    """Call dataAnalysisPlotly.py --plotly-json; return (cache_path, fig) or (None, None)."""
    log_match = re.search(r'(InstrumentDebug[\w\-\.]+\.log)', response_text)
    if not log_match:
        return None, None
    log_name = log_match.group(1)

    log_path = os.path.join(cwd, "log", log_name)
    if not os.path.isfile(log_path):
        matches = glob.glob(os.path.join(cwd, "**", log_name), recursive=True)
        log_path = matches[0] if matches else None
    if not log_path or not os.path.isfile(log_path):
        return None, None

    script = os.path.join(cwd, "scripts", "dataAnalysisPlotly.py")
    if not os.path.isfile(script):
        return None, None

    try:
        result = subprocess.run(
            [sys.executable, script, log_path, "--plotly-json"],
            capture_output=True, text=True, cwd=cwd, timeout=60,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None, None

        import plotly.io as pio
        fig = pio.from_json(result.stdout)

        cache_dir = ROOT / "data" / "plotly_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{int(time.time() * 1000)}.json"
        cache_file.write_text(result.stdout, encoding="utf-8")

        return str(cache_file), fig
    except Exception:
        return None, None


def _attach_plotly(content: str, cache_path: str) -> str:
    return f"{content}\n{_PLOTLY_MARKER}{cache_path} -->"


def _split_plotly(content: str) -> tuple[str, str | None]:
    if _PLOTLY_MARKER not in content:
        return content, None
    idx = content.index(_PLOTLY_MARKER)
    text = content[:idx].rstrip()
    marker = content[idx:]
    cache_path = marker[len(_PLOTLY_MARKER):-len(" -->")].strip()
    return text, cache_path


def _render_message(content: str):
    if _PLOTLY_MARKER in content:
        text, cache_path = _split_plotly(content)
        st.markdown(text)
        if cache_path and os.path.isfile(cache_path):
            try:
                import plotly.io as pio
                fig = pio.from_json(Path(cache_path).read_text(encoding="utf-8"))
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                pass
        return

    text, image_paths = _split_images(content)
    st.markdown(text)
    for img_path in image_paths:
        if os.path.isfile(img_path):
            st.image(img_path, caption=os.path.basename(img_path))


def relative_time(ts: float) -> str:
    diff = time.time() - ts
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff / 60)}m ago"
    if diff < 86400:
        return f"{int(diff / 3600)}h ago"
    return datetime.fromtimestamp(ts).strftime("%m/%d")


# ── Clean up interrupted streaming (e.g. user clicked Stop) ──────────────────

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
            db.update_title(cid, auto_title(title_prompt))


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
/* welcome greeting */
.welcome-card {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    border-radius: 12px;
    padding: 1.75rem 2rem;
    margin-bottom: 1.5rem;
    border: 1px solid rgba(255,255,255,0.06);
    box-shadow: 0 4px 20px rgba(0,0,0,0.15);
}
.welcome-card .greeting {
    font-size: 1.35rem;
    font-weight: 600;
    color: #e6edf3;
    margin: 0 0 0.25rem 0;
    letter-spacing: -0.02em;
}
.welcome-card .greeting .ip {
    color: #58a6ff;
    font-family: ui-monospace, monospace;
    font-weight: 500;
}
.welcome-card .sub {
    color: #8b949e;
    font-size: 0.9rem;
    margin: 0;
    line-height: 1.5;
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
    safe_ip = html.escape(client_ip)
    st.markdown(
        f'<div class="welcome-card">'
        f'<p class="greeting">Hello User,   <span class="ip">{safe_ip}</span></p>'
        f'<p class="sub">Ask questions about instruments, logs, and debugging.<br>'
        f'Start a new conversation or pick one from the sidebar.</p>'
        f'</div>',
        unsafe_allow_html=True,
    )

# Render existing messages
for msg in messages:
    with st.chat_message(msg["role"]):
        _render_message(msg["content"])

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

    # Always prepend conversation history for context
    # (--resume alone is not reliable enough in print mode)
    if messages:
        enriched = build_context_prompt(messages, enriched, prompt)

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
            full_response = f"**Error:** {proc_err}"
            db.add_message(conv_id, "assistant", full_response)
        st.rerun()

    # Save process to session state so the cleanup block can kill it on rerun
    st.session_state._streaming_proc = process
    st.session_state._streaming_conv_id = conv_id
    st.session_state._partial_response = ""
    st.session_state._streaming_auto_title_prompt = prompt

    # Stream the assistant response
    with st.chat_message("assistant"):
        response_area = st.empty()
        tool_area = st.empty()
        stop_area = st.empty()
        full_response = ""

        stop_area.button("⏹ Stop generating", key="stop_gen", type="secondary")

        for evt_type, payload in cursor_cli.iter_events(process):
            if evt_type == "text":
                full_response += payload
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
                response_area.markdown(
                    full_response or "_No response received._"
                )

        # Try interactive Plotly chart first, fall back to static images
        plotly_cache, plotly_fig = _try_interactive_plot(
            settings.get("cwd", ""), full_response,
        )
        if plotly_fig:
            st.plotly_chart(plotly_fig, use_container_width=True)
            full_response = _attach_plotly(full_response, plotly_cache)
        else:
            new_images = _find_new_images(
                settings.get("cwd", ""), request_start_time, full_response,
            )
            for img_path in new_images:
                st.image(img_path, caption=os.path.basename(img_path))
            if new_images:
                full_response = _attach_images(full_response, new_images)

    # Normal completion — clear streaming state
    st.session_state.pop("_streaming_proc", None)
    st.session_state.pop("_streaming_conv_id", None)
    st.session_state.pop("_partial_response", None)
    st.session_state.pop("_streaming_auto_title_prompt", None)

    # Persist the assistant reply
    if full_response:
        db.add_message(conv_id, "assistant", full_response)

    # Auto-title from first user message
    user_msgs = [m for m in db.get_messages(conv_id) if m["role"] == "user"]
    if len(user_msgs) == 1:
        db.update_title(conv_id, auto_title(prompt))

    st.rerun()
