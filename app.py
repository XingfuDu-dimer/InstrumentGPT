"""
Instrument GPT: Chat-style Q&A via Cursor IDE.
Flow: User question -> paste to Cursor Chat -> poll answer.md -> display.
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

ROOT = Path(__file__).resolve().parent
CURSOR_CHAT_DIR = ROOT / "cursor_chat"
INPUT_FILE = CURSOR_CHAT_DIR / "input.md"
ANSWER_FILE = CURSOR_CHAT_DIR / "answer.md"
AUTOMATE_SCRIPT = ROOT / "automate_cursor.py"
IP_SESSIONS_FILE = CURSOR_CHAT_DIR / "ip_sessions.json"
IP_EXPIRE_SECONDS = 2 * 60 * 60
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

ANSWER_ABS_PATH = str(ANSWER_FILE.resolve())


def get_client_ip() -> str | None:
    try:
        ctx = getattr(st, "context", None)
        if ctx is not None:
            return getattr(ctx, "ip_address", None)
    except Exception:
        pass
    return None


def _load_ip_sessions() -> dict[str, float]:
    try:
        if IP_SESSIONS_FILE.exists():
            data = json.loads(IP_SESSIONS_FILE.read_text(encoding="utf-8"))
            return {k: float(v) for k, v in data.items()}
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_ip_sessions(sessions: dict[str, float]) -> None:
    try:
        IP_SESSIONS_FILE.write_text(json.dumps(sessions, indent=0), encoding="utf-8")
    except OSError:
        pass


def should_new_chat(client_ip: str | None, user_wants_new_chat: bool) -> bool:
    if user_wants_new_chat:
        return True
    msgs = st.session_state.get("messages", [])
    if len(msgs) == 1:
        return True
    if not client_ip:
        return False
    now = time.time()
    sessions = _load_ip_sessions()
    last = sessions.get(client_ip, 0)
    if now - last > IP_EXPIRE_SECONDS:
        return True
    return False


def update_ip_activity(client_ip: str | None) -> None:
    if not client_ip:
        return
    sessions = _load_ip_sessions()
    sessions[client_ip] = time.time()
    _save_ip_sessions(sessions)


def get_session_id() -> str:
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())[:8]
    return st.session_state.session_id


def get_history_file() -> Path:
    return CURSOR_CHAT_DIR / f"history_{get_session_id()}.md"


def append_to_history(question: str, answer: str) -> None:
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
    q = question.lower()
    return any(k in q for k in LOG_DEBUG_KEYWORDS)


def enrich_question_with_log_mdc(question: str) -> str:
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


def build_prompt(question: str) -> str:
    enriched = enrich_question_with_log_mdc(question.strip())
    return (
        f"{enriched}\n\n"
        f"Write your answer to `{ANSWER_ABS_PATH}`."
    )


def clear_answer():
    ANSWER_FILE.write_text("", encoding="utf-8")


def trigger_cursor(prompt: str, new_chat: bool = False) -> tuple[bool, str]:
    INPUT_FILE.write_text(prompt, encoding="utf-8")
    pyperclip.copy(prompt)
    clear_answer()
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


def poll_answer(timeout_seconds: int = 120, interval: float = 1.0, status=None) -> str | None:
    deadline = time.time() + timeout_seconds
    start = time.time()

    while time.time() < deadline:
        elapsed = int(time.time() - start)
        if status is not None:
            status.update(label=f"Waiting for answer... ({elapsed}s)", state="running")

        try:
            if ANSWER_FILE.exists():
                content = ANSWER_FILE.read_text(encoding="utf-8").strip()
                if content:
                    time.sleep(1.0)
                    content2 = ANSWER_FILE.read_text(encoding="utf-8").strip()
                    return content2 if content2 and len(content2) >= len(content) else content
        except (OSError, UnicodeDecodeError):
            pass
        time.sleep(interval)

    try:
        if ANSWER_FILE.exists():
            c = ANSWER_FILE.read_text(encoding="utf-8").strip()
            if c:
                return c
    except (OSError, UnicodeDecodeError):
        pass
    return None


st.set_page_config(page_title="Instrument GPT", page_icon="🔬")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "user_wants_new_chat" not in st.session_state:
    st.session_state.user_wants_new_chat = False
if "log_mdc_tag" not in st.session_state:
    st.session_state.log_mdc_tag = DEFAULT_LOG_MDC_TAG

st.title("🔬 Instrument GPT")
st.caption("Chat with Cursor IDE to get answers")

with st.sidebar:
    st.text_input(
        "Log debug MDC tag",
        key="log_mdc_tag",
        help="Auto-attached when query mentions log download/analysis.",
        placeholder="@log-download-and-debug.mdc",
    )
    if st.button("New chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.session_id = str(uuid.uuid4())[:8]
        st.session_state.user_wants_new_chat = True
        st.rerun()
    st.divider()

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

def send_and_poll(question: str, is_retry: bool = False):
    """Send prompt to Cursor and poll for answer. Stores result in session state."""
    full_prompt = build_prompt(question)
    client_ip = get_client_ip()
    user_wants = st.session_state.get("user_wants_new_chat", False)
    new_chat = should_new_chat(client_ip, user_wants) and not is_retry
    if user_wants:
        st.session_state.user_wants_new_chat = False

    with st.status("Sending to Cursor...") as status:
        status.update(label="Switch to Cursor IDE now (Alt+Tab)...", state="running")
        time.sleep(2)
        status.update(label="Pasting to Cursor...", state="running")
        ok, err = trigger_cursor(full_prompt, new_chat=new_chat)
        if not ok:
            status.update(label="Failed", state="error")
            return None, client_ip
        answer = poll_answer(timeout_seconds=600, status=status)
        if answer:
            status.update(label="Done!", state="complete")
        else:
            status.update(label="Timeout", state="error")
        return answer, client_ip


if prompt := st.chat_input("Ask anything..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.session_state.pop("pending_retry", None)

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        answer, client_ip = send_and_poll(prompt)
        if answer:
            st.markdown(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})
            append_to_history(prompt, answer)
        else:
            st.warning("No response within 10 minutes.")
            st.session_state.pending_retry = prompt
            st.session_state.messages.append({"role": "assistant", "content": "_No response within 10 minutes._"})

        update_ip_activity(client_ip)

elif st.session_state.get("pending_retry"):
    retry_prompt = st.session_state.pending_retry
    if st.button("Retry"):
        st.session_state.pop("pending_retry")
        # Remove the timeout message from history
        if st.session_state.messages and st.session_state.messages[-1]["content"] == "_No response within 10 minutes._":
            st.session_state.messages.pop()

        clear_answer()
        with st.chat_message("assistant"):
            answer, client_ip = send_and_poll(retry_prompt, is_retry=True)
            if answer:
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
                append_to_history(retry_prompt, answer)
            else:
                st.warning("No response within 10 minutes.")
                st.session_state.pending_retry = retry_prompt
                st.session_state.messages.append({"role": "assistant", "content": "_No response within 10 minutes._"})

            update_ip_activity(client_ip)
