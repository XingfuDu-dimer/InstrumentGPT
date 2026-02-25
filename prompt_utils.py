"""Prompt building and device detection utilities."""
import re
_IP_PATTERN = re.compile(r"10\.1\.1\.(4[5-9]|50)(?!\d)")
_OCTET_TO_DEV = {
    "45": "050", "46": "051", "47": "052",
    "48": "053", "49": "054", "50": "055",
}


def extract_ip(question: str) -> tuple[str, str] | None:
    """Match 10.1.1.xx in the current message only. Returns (ip, device) or None."""
    m = _IP_PATTERN.search(question)
    if not m:
        return None
    octet = m.group(1)
    dev = _OCTET_TO_DEV.get(octet, f"0{octet}")
    return f"10.1.1.{octet}", dev


def has_device(question: str) -> bool:
    return extract_ip(question) is not None


def auto_title(question: str) -> str:
    title = question.strip().split("\n")[0]
    return (title[:47] + "...") if len(title) > 50 else (title or "New Chat")


def enrich_prompt(question: str, mdc_tag: str) -> str:
    tag = mdc_tag.strip()
    if not tag or tag in question:
        return question
    result = extract_ip(question)
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

    is_debug = has_device(raw_user_input or new_question)
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
