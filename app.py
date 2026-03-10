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
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent

import streamlit as st

import cursor_cli
import db
import knowledge
import memory
import prompt_utils
import media_utils
from ui_styles import SIDEBAR_AND_MAIN_CSS

# Default cwd: env INSTRUMENT_CWD at start, else auto-detect
DEFAULT_CWD = os.environ.get("INSTRUMENT_CWD")
if not DEFAULT_CWD or not Path(DEFAULT_CWD).exists():
    _candidate = Path(__file__).resolve().parent.parent / "Instrument"
    if _candidate.is_dir():
        DEFAULT_CWD = str(_candidate)
    elif os.name == "nt":
        DEFAULT_CWD = r"C:\Users\XingfuDu\Desktop\Instrument"
    else:
        DEFAULT_CWD = str(Path.home() / "GPT" / "Instrument")

DEFAULT_MODEL = "composer-1.5"
DEFAULT_MODE = "agent"
DEFAULT_MDC_TAG = "@log-download-and-debug.mdc"

db.init_db()


def get_client_ip() -> str:
    """Get the real client IP via the Tornado websocket request object.

    st.context.ip_address is not available in Streamlit <=1.44, so we
    reach into the runtime to read request.remote_ip from the websocket
    handler instead.
    """
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        from streamlit.runtime import get_instance
        from streamlit.web.server.browser_websocket_handler import BrowserWebSocketHandler

        ctx = get_script_run_ctx()
        if ctx is not None:
            client = get_instance().get_client(ctx.session_id)
            if isinstance(client, BrowserWebSocketHandler):
                ip = client.request.remote_ip
                if ip and ip not in ("::1",):
                    return ip
                if ip == "::1":
                    return "127.0.0.1"
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
if "viewing_example" not in st.session_state:
    st.session_state.viewing_example = None

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

# Share mode: render full conversation up to shared message, no sidebar, no chat input
if _share_mode:
    _share_messages = db.get_messages_up_to(_share_conv, _share_msg_id)
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
        st.session_state.viewing_example = None
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
                st.session_state.viewing_example = None
                st.rerun()
        with col_del:
            if st.button("×", key=f"d_{conv['id']}"):
                db.delete_conversation(conv["id"])
                if is_active:
                    st.session_state.current_conv = None
                st.rerun()

    if conversations:
        st.divider()

    _usage_examples = db.get_usage_examples()
    if _usage_examples:
        st.markdown('<p style="font-size:0.8em;color:#888;margin:0 0 4px 2px;">📝 Usage Examples</p>', unsafe_allow_html=True)
        for _ue in _usage_examples:
            _ue_active = st.session_state.viewing_example == _ue["id"]
            col_ue_title, col_ue_del = st.columns([5, 1])
            with col_ue_title:
                _ue_label = ("▸ " if _ue_active else "") + _ue["title"]
                if st.button(
                    _ue_label,
                    key=f"ue_{_ue['id']}",
                    use_container_width=True,
                ):
                    st.session_state.viewing_example = _ue["id"]
                    st.session_state.current_conv = None
                    st.rerun()
            with col_ue_del:
                if st.button("×", key=f"del_ue_{_ue['id']}"):
                    db.delete_usage_example(_ue["id"])
                    if _ue_active:
                        st.session_state.viewing_example = None
                    st.rerun()
        st.divider()

    with st.expander("📖  How to Use"):
        st.markdown("""
**Instrument GPT** helps you download instrument logs, analyze errors, plot data, and debug with your codebase — all through natural conversation.

---

#### Supported Devices

| Device | IP |
|--------|-----|
| zspr 050 | 10.1.1.85 |
| zspr 051 | 10.1.1.46 |
| zspr 052 | 10.1.1.80 |
| zspr 053 | 10.1.1.91 |
| zspr 054 | 10.1.1.93 |
| zspr 055 | 10.1.1.108 |

You can refer to a device by **name** (e.g. `zspr 052`, `52`, `052`) or by **IP** (e.g. `10.1.1.80`). The system resolves device names to IPs automatically.

---

#### Quick Start — Example Prompts

**Analyze an error** (specify device + describe the problem):
> `zspr 052 Door open timeout error, what happened?`

> `52 LED not blinking, can you check the logs?`

**Check a specific log session**:
> `52 check InstrumentDebug_2026-02-13_00-44-28.1.log for temp drop`

**Paste log content for analysis** (include device):
> `zspr 052 [2026-02-13 05:04:52.782][debug] temp 61.9, next line temp 29.4 — why the sudden drop?`

**Plot PID / temperature control data**:
> `52 plot temp control`

> `50 plot PID`

**Download all logs from a device**:
> `52 download all logs`

**General questions (no device needed)**:
> `What causes a Door timeout error?`

> `How does the DoorController handle initialization?`

---

#### Save & Share

- **💾 Save** — click the save button on any assistant answer to generate a summarized knowledge document (saved to `liked_answers/`). Useful for future reference.
- **🔗 Share** — click the link button to copy a shareable URL. Anyone with the link can view the conversation up to that answer (read-only).

---

#### Tips
- **Include the device** (name or IP) in your question to trigger automatic log download and analysis.
- **Without a device**, the assistant answers from general knowledge and the codebase only (no download).
- After downloading, the assistant analyzes the logs, cross-references your source code, and reports root cause, timeline, and fix suggestions.
- **Interactive charts**: When you ask to plot data, the chart supports zoom, pan, and hover — use your mouse to explore.
- **Memory**: The assistant remembers what was downloaded and analyzed earlier in the conversation. No need to re-specify the device or re-download logs unless you want fresh data.
- **Settings** (below): Change model, mode, MDC tag, and working directory. Settings are saved per user.

---

#### 💡 Add Your Own Example

In any conversation, type **"add this to usage example"** and the entire conversation will be saved here for everyone to see.
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

# ── Usage example view (read-only) ───────────────────────────────────────────
if st.session_state.viewing_example:
    _all_examples = {e["id"]: e for e in db.get_usage_examples()}
    _sel_example = _all_examples.get(st.session_state.viewing_example)
    if _sel_example:
        st.markdown(f"### 📝 {_sel_example['title']}")
        st.divider()
        try:
            _example_msgs = json.loads(_sel_example["content"])
        except (json.JSONDecodeError, TypeError):
            _example_msgs = None
        if _example_msgs and isinstance(_example_msgs, list):
            for _emsg in _example_msgs:
                with st.chat_message(_emsg.get("role", "user")):
                    media_utils.render_message(_emsg.get("content", ""))
        else:
            st.markdown(_sel_example["content"], unsafe_allow_html=False)
        st.stop()
    else:
        st.session_state.viewing_example = None

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
            toast.textContent = ok ? "Link copied!" : "Copy failed – select manually and press Ctrl+C";
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

    # ── "Add to usage example" interception ──────────────────────────────
    if prompt_utils.is_add_usage_example(prompt) and conv_id and messages:
        conv_title = (conv_info or {}).get("title", "Usage Example")
        example_content = prompt_utils.format_conversation_as_example(messages)
        db.add_usage_example(
            title=conv_title,
            content=example_content,
            source_conv_id=conv_id,
            created_by_ip=client_ip,
        )
        db.add_message(conv_id, "user", prompt)
        confirm_msg = f"✅ This conversation has been added to **How to Use** as an example: **{conv_title}**"
        db.add_message(conv_id, "assistant", confirm_msg)
        st.toast("Added to usage examples!")
        st.rerun()

    # Persist & show the user message
    db.add_message(conv_id, "user", prompt)
    with st.chat_message("user"):
        st.markdown(prompt)

    # Build enriched prompt (always include context so Agent has mdc_tag, cwd, device)
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
        show_file_paths: list[str] = []
        plotly_json_paths: list[str] = []

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

            elif evt_type == "show_file":
                show_file_paths.append(payload)

            elif evt_type == "plotly_json":
                plotly_json_paths.append(payload)

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

        _cwd = settings.get("cwd", "")

        # show_file events: render raw content directly (deduplicate paths)
        _seen_show: set[str] = set()
        deduped_show_paths: list[str] = []
        for p in show_file_paths:
            norm = os.path.normpath(os.path.join(_cwd, p)) if not os.path.isabs(p) else os.path.normpath(p)
            if norm not in _seen_show:
                _seen_show.add(norm)
                deduped_show_paths.append(p)

        if deduped_show_paths:
            rendered_paths: list[str] = []
            for rel_path in deduped_show_paths:
                abs_path = os.path.normpath(os.path.join(_cwd, rel_path)) if not os.path.isabs(rel_path) else rel_path
                if not os.path.isfile(abs_path):
                    continue
                rendered_paths.append(abs_path)
                try:
                    raw = Path(abs_path).read_text(encoding="utf-8", errors="ignore")
                    name = os.path.basename(abs_path)
                    is_config = media_utils._is_config_file(abs_path)
                    with st.expander(f"📄 {name}", expanded=not is_config):
                        if abs_path.lower().endswith(".json"):
                            st.json(json.loads(raw))
                        else:
                            st.code(raw[:50000], language=media_utils.lang_for_file(name))
                except (json.JSONDecodeError, OSError):
                    pass
            if rendered_paths:
                full_response = media_utils.attach_files(full_response, rendered_paths)

        # Plotly: try intercepted paths first, then scan response text + timestamp
        plotly_cache, plotly_fig, plotly_html_path = None, None, None
        if plotly_json_paths:
            for pjp in plotly_json_paths:
                abs_pjp = os.path.normpath(os.path.join(_cwd, pjp)) if not os.path.isabs(pjp) else pjp
                if os.path.isfile(abs_pjp):
                    plotly_cache, plotly_fig, _ = media_utils.try_interactive_plot(
                        _cwd, f"Saved: {pjp}", since=0,
                    )
                    if plotly_fig:
                        break
        if not plotly_fig:
            plotly_cache, plotly_fig, plotly_html_path = media_utils.try_interactive_plot(_cwd, full_response, since=request_start_time)
        if plotly_fig:
            st.plotly_chart(plotly_fig, use_container_width=True, key=f"plotly_{int(time.time()*1000)}")
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
