"""Knowledge base — AI summarization of liked conversations.

Runs as a subprocess worker: `python -m knowledge summarize <conv_id> <cwd>`
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

_SUMMARIZE_PROMPT = """Summarize the following conversation into a knowledge document (Markdown).

CRITICAL: Output the FULL Markdown content directly in your response.
- Do NOT write to any file. Do NOT use write/edit tools.
- Your response will be saved automatically to the knowledge base.
- Output ONLY the document content — no "I have written..." or file paths.

Requirements:
- Extract key conclusions, root causes, and steps (do not copy verbatim)
- Clear structure: background, analysis, key steps
- Suitable for future reference

Conversation:
<conversation>
{conversation}
</conversation>

Output the complete Markdown document in your response. No tools. No file paths."""


def _build_conversation_text(messages: list[dict]) -> str:
    """Build filtered conversation text for summarization prompt."""
    from memory import filter_content

    parts = []
    for msg in messages:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = filter_content(msg["content"])
        if content:
            parts.append(f"## {role}\n{content}")
    return "\n\n".join(parts) if parts else "(empty)"


def _run_summarization_worker(conv_id: str, last_message_id: int, cwd: str) -> str | None:
    """Run summarization in this process. Returns file_path on success, None on failure."""
    import db
    import cursor_cli

    entry = db.get_liked_entry(conv_id, last_message_id)
    if not entry or entry["status"] not in ("pending", "summarizing"):
        return None

    messages = db.get_messages_up_to(conv_id, last_message_id)
    if not messages:
        db.update_liked_status(conv_id, last_message_id, "cancelled")
        return None

    conv_text = _build_conversation_text(messages)
    prompt = _SUMMARIZE_PROMPT.format(conversation=conv_text)

    process, err = cursor_cli.create_process(
        prompt=prompt,
        cwd=cwd or str(ROOT),
        model=None,
        mode="ask",
        resume_session=None,
    )
    if err:
        db.update_liked_status(conv_id, last_message_id, "cancelled")
        return None

    full_response = ""
    for evt_type, payload in cursor_cli.iter_events(process):
        if evt_type == "text":
            full_response += payload
        elif evt_type == "text_replace":
            full_response = payload
        elif evt_type == "error" and not full_response:
            full_response = f"Error: {payload}"
        elif evt_type == "done":
            break

    if not full_response.strip():
        db.update_liked_status(conv_id, last_message_id, "cancelled")
        return None

    # Write MD file
    out_dir = Path(cwd or str(ROOT)) / "liked_answers"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    conv_info = db.get_conversation(conv_id)
    title = (conv_info or {}).get("title", "Untitled")[:50]
    ip_address = (conv_info or {}).get("ip_address", "")
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
    file_path = out_dir / f"liked_{safe_title}_{ts}.md"

    header = f"""# {title}

> IP: {ip_address} · conv: {conv_id} · msg: {last_message_id} · {datetime.now().strftime("%Y-%m-%d %H:%M")}

---

"""
    file_path.write_text(header + full_response.strip(), encoding="utf-8")
    return str(file_path)


def start_summarization(conv_id: str, last_message_id: int, cwd: str) -> tuple[bool, str]:
    """Spawn background subprocess for summarization.

    Returns (success, message). On success, subprocess runs independently.
    """
    import db

    existing = db.get_liked_entry(conv_id, last_message_id)
    if existing:
        if existing["status"] in ("pending", "summarizing"):
            return False, "Already summarizing."
        if existing["status"] == "completed":
            return False, "Already in base."

    cwd = cwd or str(ROOT)
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "knowledge", "summarize", conv_id, str(last_message_id), cwd],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        db.create_liked_entry(conv_id, last_message_id, worker_pid=proc.pid)
        return True, "Adding…"
    except Exception as e:
        return False, str(e)


def cancel_or_unlike(conv_id: str, last_message_id: int) -> tuple[bool, str]:
    """Cancel (if in progress) or Unlike (if completed). Returns (success, message)."""
    import db

    entry = db.get_liked_entry(conv_id, last_message_id)
    if not entry:
        return False, "Not in base."

    if entry["status"] in ("pending", "summarizing") and entry.get("worker_pid"):
        try:
            sig = getattr(signal, "SIGKILL", signal.SIGTERM)
            os.kill(entry["worker_pid"], sig)
        except (ProcessLookupError, OSError, AttributeError):
            pass
        db.update_liked_status(conv_id, last_message_id, "cancelled")
        db.delete_liked_entry(conv_id, last_message_id)
        return True, "Cancelled."

    if entry["status"] == "completed" and entry.get("file_path"):
        path = Path(entry["file_path"])
        if path.exists():
            path.unlink()
        db.delete_liked_entry(conv_id, last_message_id)
        return True, "Removed."

    db.delete_liked_entry(conv_id, last_message_id)
    return True, "Removed."


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "summarize":
        import db

        db.init_db()
        conv_id = sys.argv[2]
        last_message_id = int(sys.argv[3])
        cwd = sys.argv[4]

        try:
            path = _run_summarization_worker(conv_id, last_message_id, cwd)
            if path:
                db.update_liked_status(conv_id, last_message_id, "completed", file_path=path)
            else:
                db.update_liked_status(conv_id, last_message_id, "cancelled")
        except Exception:
            db.update_liked_status(conv_id, last_message_id, "cancelled")
            raise
