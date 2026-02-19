"""
Instrument GPT: Chat-style Q&A via Cursor IDE.
Flow: User question -> paste to Cursor Chat -> poll answer.md -> append to conversation.
Each session gets its own answer file (answer_<session_id>.md) to avoid overwriting.
Q&A history is persisted to cursor_chat/history_<session_id>.md.
New chat: user clicks "New chat", or first question, or same IP idle > 2 hours.
"""
import json
import sys
import time
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st
import pyperclip

# Project root
ROOT = Path(__file__).resolve().parent
CURSOR_CHAT_DIR = ROOT / "cursor_chat"
INPUT_FILE = CURSOR_CHAT_DIR / "input.md"
AUTOMATE_SCRIPT = ROOT / "automate_cursor.py"
IP_SESSIONS_FILE = CURSOR_CHAT_DIR / "ip_sessions.json"
IP_EXPIRE_SECONDS = 2 * 60 * 60  # 2 hours
DEFAULT_LOG_MDC_TAG = "@log-download-and-debug.mdc"

CURSOR_CHAT_DIR.mkdir(exist_ok=True)

LOG_DEBUG_KEYWORDS = (
    "download latest log",
    "download logs",
    "latest log",
    "service bundle",
    "systemhealth",
    "debug log",
    "analyze log",
    "log analysis",
)


def get_client_ip() -> str | None:
    """Get client IP. Streamlit 1.54+ has st.context.ip_address."""
    try:
        ctx = getattr(st, "context", None)
        if ctx is not None:
            return getattr(ctx, "ip_address", None)
    except Exception:
        pass
    return None


def _load_ip_sessions() -> dict[str, float]:
    """Load IP -> last_activity timestamp from file."""
    try:
        if IP_SESSIONS_FILE.exists():
            data = json.loads(IP_SESSIONS_FILE.read_text(encoding="utf-8"))
            return {k: float(v) for k, v in data.items()}
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_ip_sessions(sessions: dict[str, float]) -> None:
    """Save IP sessions to file."""
    try:
        IP_SESSIONS_FILE.write_text(json.dumps(sessions, indent=0), encoding="utf-8")
    except OSError:
        pass


def should_new_chat(client_ip: str | None, user_wants_new_chat: bool) -> bool:
    """
    True if we should open a new Cursor chat.
    - user_wants_new_chat: user clicked "New chat" button
    - first question in session: len(messages)==1
    - same IP: session expired if idle > 2 hours (by IP last_activity)
    """
    if user_wants_new_chat:
        return True
    msgs = st.session_state.get("messages", [])
    if len(msgs) == 1:
        return True  # first question in this chat
    if not client_ip:
        return False  # no IP: continue current chat
    now = time.time()
    sessions = _load_ip_sessions()
    last = sessions.get(client_ip, 0)
    if now - last > IP_EXPIRE_SECONDS:
        return True  # expired
    return False


def update_ip_activity(client_ip: str | None) -> None:
    """Update last activity timestamp for client IP."""
    if not client_ip:
        return
    sessions = _load_ip_sessions()
    sessions[client_ip] = time.time()
    _save_ip_sessions(sessions)


def get_session_id() -> str:
    """Get or create session ID."""
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())[:8]
    return st.session_state.session_id


def get_session_answer_file() -> Path:
    """Get answer file path for current session (unique per browser tab/connection)."""
    return CURSOR_CHAT_DIR / f"answer_{get_session_id()}.md"


def get_target_answer_file() -> Path:
    """Get answer file path for current session."""
    return get_session_answer_file()


def get_history_file() -> Path:
    """Get history file path for current session."""
    return CURSOR_CHAT_DIR / f"history_{get_session_id()}.md"


def append_to_history(question: str, answer: str) -> None:
    """Append Q&A pair to session history file."""
    hist_file = get_history_file()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = f"""
---
## Q ({ts})
{question}

## A
{answer}
"""
    with open(hist_file, "a", encoding="utf-8") as f:
        f.write(block)


def should_attach_log_mdc(question: str) -> bool:
    """Return True when question likely asks for log download/analysis workflow."""
    q = question.lower()
    return any(k in q for k in LOG_DEBUG_KEYWORDS)


def enrich_question_with_log_mdc(question: str) -> str:
    """Auto-attach log debug mdc reference when query indicates log troubleshooting."""
    tag = st.session_state.get("log_mdc_tag", DEFAULT_LOG_MDC_TAG).strip() or DEFAULT_LOG_MDC_TAG
    if tag in question:
        return question
    if not should_attach_log_mdc(question):
        return question
    return (
        f"Use {tag} as the primary guide for downloading latest logs and debugging.\n"
        f"Follow its instrument/IP mapping and workflow.\n\n"
        f"{question}"
    )


def build_prompt(question: str, answer_file: Path) -> str:
    """Build prompt sent to Cursor."""
    enriched_question = enrich_question_with_log_mdc(question.strip())
    answer_filename = answer_file.name
    write_hint = (
        "\n\nPlease write your final answer to "
        f"`cursor_chat/{answer_filename}` in this repo."
    )
    return enriched_question + write_hint


def clear_answer(answer_file: Path):
    """Clear answer file to detect new response."""
    if answer_file.exists():
        answer_file.write_text("", encoding="utf-8")


def trigger_cursor(prompt: str, answer_file: Path, new_chat: bool = False) -> tuple[bool, str]:
    """Write to input, copy to clipboard, run automation script."""
    INPUT_FILE.write_text(prompt, encoding="utf-8")
    pyperclip.copy(prompt)
    clear_answer(answer_file)
    args = [sys.executable, str(AUTOMATE_SCRIPT), str(INPUT_FILE)]
    if new_chat:
        args.append("--new-chat")
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(ROOT),
        )
        if result.returncode != 0:
            return False, result.stderr or "Automation script failed"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "Automation script timed out"
    except Exception as e:
        return False, str(e)


def _read_content(path: Path) -> str | None:
    """Read file content; return None on error or empty."""
    try:
        if path.exists():
            c = path.read_text(encoding="utf-8").strip()
            return c if c else None
    except (OSError, UnicodeDecodeError):
        pass
    return None


def _candidate_answer_files(answer_file: Path) -> list[Path]:
    """Return possible answer files to scan, including session file and all answer*.md."""
    candidates: list[Path] = [answer_file.resolve(), (CURSOR_CHAT_DIR / "answer.md").resolve()]
    # Scan local project answer files as fallback.
    for path in CURSOR_CHAT_DIR.glob("answer*.md"):
        resolved = path.resolve()
        if resolved not in candidates:
            candidates.append(resolved)
    # If target answer file is outside current project, also scan sibling answer*.md there.
    parent = answer_file.resolve().parent
    if parent != CURSOR_CHAT_DIR.resolve():
        try:
            for path in parent.glob("answer*.md"):
                resolved = path.resolve()
                if resolved not in candidates:
                    candidates.append(resolved)
        except OSError:
            pass
    return candidates


def poll_answer(
    answer_file: Path,
    timeout_seconds: int = 120,
    interval: float = 1.0,
    status=None,
    request_started_at: float | None = None,
) -> str | None:
    """
    Poll answer file(s) until content exists or timeout.
    Scans session answer file + answer.md + all answer*.md files.
    If request_started_at is provided, prefer files updated after this request started.
    """
    deadline = time.time() + timeout_seconds
    start = time.time()

    while time.time() < deadline:
        elapsed = int(time.time() - start)
        if status is not None:
            status.update(label=f"Waiting for answer... ({elapsed}s)", state="running")

        for path in _candidate_answer_files(answer_file):
            if request_started_at is not None:
                try:
                    if path.stat().st_mtime + 0.2 < request_started_at:
                        continue
                except OSError:
                    continue
            content = _read_content(path)
            if content:
                time.sleep(0.8)
                content2 = _read_content(path)
                return (content2 if content2 and len(content2) >= len(content) else content) or content
        time.sleep(interval)

    for path in _candidate_answer_files(answer_file):
        c = _read_content(path)
        if c:
            return c
    return None


st.set_page_config(page_title="Instrument GPT", page_icon="🔬")

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []
if "user_wants_new_chat" not in st.session_state:
    st.session_state.user_wants_new_chat = False
if "log_mdc_tag" not in st.session_state:
    st.session_state.log_mdc_tag = DEFAULT_LOG_MDC_TAG

# Header
st.title("🔬 Instrument GPT")
st.caption("Chat with Cursor IDE to get answers")

# Sidebar: New chat + History record
with st.sidebar:
    st.text_input(
        "Log debug MDC tag",
        key="log_mdc_tag",
        help="Auto-attached when query mentions log download/analysis.",
        placeholder="@log-download-and-debug.mdc",
    )
    if st.button("🆕 New chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.session_id = str(uuid.uuid4())[:8]
        st.session_state.user_wants_new_chat = True
        st.rerun()
    st.divider()

# Render chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat input
if prompt := st.chat_input("Ask anything... (Switch to Cursor IDE when prompted)"):
    # Add user message
    st.session_state.messages.append({"role": "user", "content": prompt})

    # Display user message
    with st.chat_message("user"):
        st.markdown(prompt)

    # Get answer from Cursor
    answer_file = get_target_answer_file()
    answer_filename = answer_file.name
    full_prompt = build_prompt(prompt, answer_file)
    response = ""
    with st.chat_message("assistant"):
        st.caption(f"Listening for answer file: `{answer_file.resolve()}`")
        with st.status("Sending to Cursor...") as status:
            status.update(label="Switch to Cursor IDE now (Alt+Tab)...", state="running")
            time.sleep(2)
            status.update(label="Pasting to Cursor...", state="running")
            client_ip = get_client_ip()
            user_wants = st.session_state.get("user_wants_new_chat", False)
            new_chat = should_new_chat(client_ip, user_wants)
            if user_wants:
                st.session_state.user_wants_new_chat = False
            request_started_at = time.time()
            ok, err = trigger_cursor(full_prompt, answer_file, new_chat=new_chat)
            if not ok:
                status.update(label="Failed", state="error")
                response = "_Could not send to Cursor. Ensure Cursor is open and try again. You can manually copy the prompt from `cursor_chat/input.md`._"
            else:
                answer = poll_answer(
                    answer_file,
                    timeout_seconds=120,
                    status=status,
                    request_started_at=request_started_at,
                )
                if answer:
                    status.update(label="Done!", state="complete")
                    response = answer
                else:
                    status.update(label="Timeout", state="error")
                    response = f"_No response within 2 minutes. Check `cursor_chat/{answer_filename}` manually._"
                    path = answer_file.resolve()
                    if path.exists():
                        content = path.read_text(encoding="utf-8")
                        if content:
                            response = content

        st.markdown(response)

    # Append to in-memory chat and persist to history file
    st.session_state.messages.append({"role": "assistant", "content": response})
    append_to_history(prompt, response)
    update_ip_activity(client_ip)
