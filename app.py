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
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Add Instrument/scripts to path for show_device_data, show_file (live in Instrument repo)
# Must run before importing those modules
for _p in (ROOT.parent / "Instrument" / "scripts", Path(os.environ.get("INSTRUMENT_CWD", "")) / "scripts"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
        break

import streamlit as st

import cursor_cli
import db
import knowledge
import show_device_data
import show_file
import memory
import prompt_utils
import media_utils
from ui_styles import SIDEBAR_AND_MAIN_CSS

# Default cwd: env INSTRUMENT_CWD at start, else ROOT
DEFAULT_CWD = os.environ.get("INSTRUMENT_CWD")
if not DEFAULT_CWD or not Path(DEFAULT_CWD).exists():
    DEFAULT_CWD = r"C:\Users\XingfuDu\Desktop\Instrument"

DEFAULT_MODEL = "composer-1.5"
DEFAULT_MODE = "agent"
DEFAULT_MDC_TAG = "@log-download-and-debug.mdc"

db.init_db()


def _config_path_for_type(cwd: str, ip: str, data_type: str) -> str | None:
    """Return absolute path for config file, or None for SystemHealth (dynamic)."""
    base = Path(cwd) / "device" / ip
    if data_type == "InstrumentParameters":
        return str((base / "config" / "InstrumentParameters.json").resolve())
    if data_type == "SystemHealthParameters":
        return str((base / "config" / "SystemHealthParameters.json").resolve())
    if data_type == "SystemHistory":
        return str((base / "SystemHealth" / "SystemHistory.json").resolve())
    if data_type == "SystemHealth":
        files = sorted((base / "SystemHealth").glob("SystemHealth_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return str(files[0].resolve()) if files else None
    return None


def _config_label_for_type(data_type: str) -> str:
    return {"InstrumentParameters": "InstrumentParameters", "SystemHealthParameters": "SystemHealthParameters", "SystemHealth": "SystemHealth", "SystemHistory": "SystemHistory"}.get(data_type, data_type)


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

st.set_page_config(page_title="Instrument GPT", page_icon="🔬", layout="wide", initial_sidebar_state="expanded")
st.markdown(SIDEBAR_AND_MAIN_CSS, unsafe_allow_html=True)

# ── Session state defaults ───────────────────────────────────────────────────

if "current_conv" not in st.session_state:
    st.session_state.current_conv = None

# ── Share link: ?conv=xxx&msg=yyy → read-only shared view ───────────────────
params = st.query_params
_share_mode = False
if "conv" in params and "msg" in params:
    _share_conv = params["conv"]
    _share_conv_info = db.get_conversation(_share_conv)
    try:
        _share_msg_id = int(params["msg"])
    except ValueError:
        _share_msg_id = None
    if _share_conv_info and _share_msg_id:
        _share_mode = True
elif "conv" in params:
    _share_conv = params["conv"]
    if db.get_conversation(_share_conv):
        st.session_state.current_conv = _share_conv

client_ip = get_client_ip()

# Load settings from DB (persists across page refresh); fallback to defaults
if "settings" not in st.session_state:
    defaults = {
        "model": DEFAULT_MODEL,
        "mode": DEFAULT_MODE,
        "mdc_tag": DEFAULT_MDC_TAG,
        "cwd": DEFAULT_CWD,
    }
    saved = db.get_user_settings(client_ip)
    if saved:
        st.session_state.settings = {
            k: (saved.get(k) or defaults[k]) for k in defaults
        }
    else:
        st.session_state.settings = dict(defaults)

# Share mode: render Q&A only, no sidebar, no chat input
if _share_mode:
    _share_messages = db.get_qa_pair(_share_conv, _share_msg_id)
    if _share_messages:
        for msg in _share_messages:
            with st.chat_message(msg["role"]):
                media_utils.render_message(msg["content"])
    st.stop()

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
        st.session_state.settings["model"] = st.text_input(
            "Model",
            value=st.session_state.settings["model"],
            help="CLI model ID (e.g. composer-1.5, sonnet-4.6). Empty = Auto. Run `agent models` to list.",
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
        # Persist settings to DB so they survive page refresh
        db.save_user_settings(client_ip, st.session_state.settings)

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

# Render existing messages with per-answer save
# Use fragment to auto-refresh save status when summarization is in progress
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
                with st.container(key=f"action_btns_{mid}"):
                    btn_col1, btn_col2 = st.columns(2)
                    with btn_col1:
                        if not entry:
                            if st.button("💾", key=f"like_{mid}", help="Save to knowledge base", type="secondary"):
                                ok, m = knowledge.start_summarization(conv_id, mid, cwd)
                                st.toast(m)
                                st.rerun()
                        elif entry["status"] in ("pending", "summarizing"):
                            if st.button("💾 … ✕", key=f"cancel_{mid}", help="Cancel saving"):
                                ok, m = knowledge.cancel_or_unlike(conv_id, mid)
                                st.toast(m)
                                st.rerun()
                        else:
                            if st.button("✓", key=f"unlike_{mid}", help="Saved · click to remove", type="secondary"):
                                ok, m = knowledge.cancel_or_unlike(conv_id, mid)
                                st.toast(m)
                                st.rerun()
                    with btn_col2:
                        if st.button("🔗", key=f"share_{mid}", help="Copy share link", type="secondary"):
                            st.session_state["_copy_share"] = f"__DYNAMIC__?conv={conv_id}&msg={mid}"
                            st.rerun()


_messages_with_likes()

_share_path = st.session_state.pop("_copy_share", None)
if _share_path:
    _qs = _share_path.split("?", 1)[1] if "?" in _share_path else ""
    st.components.v1.html(
        f"""<script>
        (function(){{
            var qs = "?{_qs}";
            var pdoc = window.parent.document;
            var origin = "";
            try {{ origin = window.parent.location.origin; }} catch(e) {{
                origin = window.location.origin;
            }}
            var url = origin + "/" + qs;

            var ok = false;
            var ta = pdoc.createElement("textarea");
            ta.value = url;
            ta.style.position = "fixed";
            ta.style.left = "-9999px";
            pdoc.body.appendChild(ta);
            ta.select();
            try {{ ok = pdoc.execCommand("copy"); }} catch(e) {{}}
            pdoc.body.removeChild(ta);
            if (!ok && window.parent.navigator.clipboard && window.parent.navigator.clipboard.writeText) {{
                window.parent.navigator.clipboard.writeText(url).then(function(){{ ok = true; }}).catch(function(){{}});
            }}

            var toast = pdoc.createElement("div");
            toast.textContent = ok ? "Link copied!" : "請手動選取後 Ctrl+C";
            toast.style.cssText = "position:fixed;top:16px;right:20px;padding:8px 16px;background:#262730;color:#fafafa;border-radius:6px;font-size:13px;z-index:999999;font-family:sans-serif;box-shadow:0 2px 12px rgba(0,0,0,0.3);";
            pdoc.body.appendChild(toast);
            setTimeout(function(){{ toast.remove(); }}, 1500);
        }})();
        </script>""",
        height=0,
    )

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
    enriched = prompt_utils.enrich_prompt(prompt, settings.get("mdc_tag", ""), settings.get("cwd", ""))
    cli_session = conv_info.get("cli_session_id") if conv_info else None

    # Load memory and build structured context
    existing_summary, state_json = db.get_memory(conv_id)
    diag_state = memory.DiagnosticState.deserialize(state_json)

    _cwd = settings.get("cwd", "")
    ip_result = prompt_utils.extract_ip(prompt)
    if ip_result:
        diag_state.device_ip = ip_result[0]
        diag_state.device_name = f"zspr {ip_result[1]}"
    else:
        # Resolve device number (50, 52, etc.) via device_mapping for diag_state
        resolved = show_device_data.resolve_ip(prompt, _cwd, None, messages)
        if resolved:
            diag_state.device_ip = resolved

    # Short-circuit: "show me config" → run show_device_data.sh directly, display full JSON
    if show_device_data.is_show_config_intent(prompt):
        ip = show_device_data.resolve_ip(prompt, _cwd, diag_state.device_ip, messages)
        if ip:
            diag_state.device_ip = ip
            data_type = show_device_data.parse_config_type(prompt)
            ok, output, err = show_device_data.run_show_device_data(_cwd, ip, data_type)
            if ok:
                try:
                    data = json.loads(output)
                    config_path = _config_path_for_type(_cwd, ip, data_type)
                    label = _config_label_for_type(data_type)
                    msg = f"**{label}** for device ({ip}):\n\n"
                    msg = media_utils.attach_config_files(msg, [config_path] if config_path else [])
                    db.add_message(conv_id, "assistant", msg)
                    db.update_memory(conv_id, existing_summary, diag_state.serialize())
                    with st.chat_message("assistant"):
                        st.markdown(media_utils._strip_markers(msg))
                        with st.expander(f"📄 {label} (full)", expanded=True):
                            st.json(data)
                    st.rerun()
                except json.JSONDecodeError:
                    msg = f"**{data_type}** for {ip}:\n\n```json\n{output[:50000]}\n```"
                    if len(output) > 50000:
                        msg += "\n\n*(truncated)*"
                    db.add_message(conv_id, "assistant", msg)
                    db.update_memory(conv_id, existing_summary, diag_state.serialize())
                    with st.chat_message("assistant"):
                        st.markdown(msg)
                    st.rerun()
            else:
                db.add_message(conv_id, "assistant", f"**Error:** {err}\n\nRun `download_config.sh {ip}` first if config not yet downloaded.")
                with st.chat_message("assistant"):
                    st.markdown(f"**Error:** {err}")
                    st.info(f"Run `download_config.sh {ip}` first if config not yet downloaded.")
                st.rerun()
        # else: no IP resolved, fall through to agent

    # Short-circuit: "show me <path>" → display arbitrary file (skip oversized logs)
    if show_file.is_show_file_intent(prompt):
        paths = show_file.extract_file_paths(prompt, _cwd, messages)
        displayable: list[tuple[str, str, bool]] = []  # (path, content, is_json)
        skipped: list[str] = []
        for p in paths:
            ok, reason = show_file.can_display_file(p)
            if not ok:
                skipped.append(f"{os.path.basename(p)}: {reason}")
                continue
            content, is_json = show_file.read_file_for_display(p)
            if content:
                displayable.append((p, content, is_json))
        if displayable:
            parts = []
            for path, content, is_json in displayable:
                name = os.path.basename(path)
                if is_json:
                    try:
                        data = json.loads(content)
                        parts.append((path, f"**{name}**\n\n", data, True))
                    except json.JSONDecodeError:
                        parts.append((path, f"**{name}**\n\n", content, False))
                else:
                    parts.append((path, f"**{name}**\n\n", content, False))
            msg_parts = []
            file_paths = []
            for path, prefix, data_or_text, is_json in parts:
                msg_parts.append(prefix)
                file_paths.append(path)
            msg = "".join(msg_parts)
            msg = media_utils.attach_files(msg, file_paths)
            if skipped:
                msg += "\n\n*Skipped (too large):* " + "; ".join(skipped)
            db.add_message(conv_id, "assistant", msg)
            db.update_memory(conv_id, existing_summary, diag_state.serialize())
            with st.chat_message("assistant"):
                st.markdown(media_utils._strip_markers(msg))
                for path, prefix, data_or_text, is_json in parts:
                    name = os.path.basename(path)
                    with st.expander(f"📄 {name}", expanded=True):
                        if is_json:
                            st.json(data_or_text)
                        else:
                            lang = "log" if name.endswith(".log") else "text"
                            media_utils.code_with_copy(data_or_text, language=lang)
                if skipped:
                    st.caption("Skipped (too large): " + "; ".join(skipped))
            st.rerun()
        elif skipped and not displayable:
            err_msg = "Skipped (too large): " + "; ".join(skipped)
            db.add_message(conv_id, "assistant", err_msg)
            with st.chat_message("assistant"):
                st.warning(err_msg)
            st.rerun()

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
        media_utils.code_with_copy(enriched, language="markdown")

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

        # Plotly/images/config
        _cwd = settings.get("cwd", "")
        plotly_cache, plotly_fig, plotly_html_path = media_utils.try_interactive_plot(_cwd, full_response)
        if plotly_fig:
            st.plotly_chart(plotly_fig, use_container_width=True, key=f"plotly_live_{conv_id}")
            full_response = media_utils.attach_plotly(full_response, plotly_cache)
        elif plotly_html_path:
            html_content = Path(plotly_html_path).read_text(encoding="utf-8", errors="ignore")
            st.components.v1.html(html_content, height=1200, scrolling=False)
            full_response = media_utils.attach_plotly_html(full_response, plotly_html_path)
        else:
            new_images = media_utils.find_new_images(
                settings.get("cwd", ""), request_start_time, full_response,
            )
            for img_path in new_images:
                st.image(img_path, caption=os.path.basename(img_path))
            if new_images:
                full_response = media_utils.attach_images(full_response, new_images)
        new_configs = media_utils.find_new_config_files(_cwd, request_start_time)
        refd_configs = media_utils.find_config_paths_in_response(_cwd, full_response)
        all_configs = list(dict.fromkeys(new_configs + refd_configs))
        if all_configs:
            for path in all_configs:
                if os.path.isfile(path):
                    try:
                        data = json.loads(Path(path).read_text(encoding="utf-8", errors="ignore"))
                        with st.expander(f"📄 {os.path.basename(path)}", expanded=True):
                            st.json(data)
                    except (json.JSONDecodeError, OSError):
                        pass
            full_response = media_utils.attach_config_files(full_response, all_configs)

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
