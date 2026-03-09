"""Prompt building and device detection utilities."""
import re

_DEV_TO_IP = {
    "50": "10.1.1.85",  "51": "10.1.1.46",  "52": "10.1.1.80",
    "53": "10.1.1.91",  "54": "10.1.1.93",  "55": "10.1.1.108",
    "050": "10.1.1.85", "051": "10.1.1.46", "052": "10.1.1.80",
    "053": "10.1.1.91", "054": "10.1.1.93", "055": "10.1.1.108",
}
_IP_TO_DEV = {ip: dev for dev, ip in _DEV_TO_IP.items() if len(dev) == 3}

_IP_PATTERN = re.compile(
    r"10\.1\.1\.(?:" + "|".join(
        re.escape(ip.split(".")[-1]) for ip in sorted(set(_DEV_TO_IP.values()))
    ) + r")(?!\d)"
)
_DEVICE_NUM_RE = re.compile(r"\b(?:zspr\s*)?0?(5[0-5])\b", re.IGNORECASE)

_USAGE_EXAMPLE_RE = re.compile(
    r"(?:add|save|put|store|append)\b.*\b(?:usage\s*example|how\s*to\s*use|example)",
    re.IGNORECASE,
)


def extract_device(question: str) -> tuple[str, str] | None:
    """Extract device info from question. Returns (ip, dev_number) or None.

    Matches IP addresses (10.1.1.x) or device numbers (50-55, 050-055, zspr 0xx).
    """
    m = _IP_PATTERN.search(question)
    if m:
        ip = m.group(0)
        dev = _IP_TO_DEV.get(ip, "")
        return ip, dev

    m = _DEVICE_NUM_RE.search(question)
    if m:
        num = m.group(1)
        ip = _DEV_TO_IP.get(num, "")
        dev = f"0{num}"
        return ip, dev

    return None


def extract_ip(question: str) -> tuple[str, str] | None:
    """Backward-compatible alias for extract_device."""
    return extract_device(question)


def has_device(question: str) -> bool:
    """True if the question references a device by IP or number (50-55, zspr)."""
    return extract_device(question) is not None


def auto_title(question: str) -> str:
    title = question.strip().split("\n")[0]
    return (title[:47] + "...") if len(title) > 50 else (title or "New Chat")


def enrich_prompt(question: str, mdc_tag: str, cwd: str = "") -> str:
    tag = mdc_tag.strip()
    if not tag or tag in question:
        return question
    cwd_note = f"Your working directory is `{cwd}`. " if cwd else ""
    result = extract_device(question)
    if result:
        ip, dev = result
        device_label = f"zspr {dev} ({ip})" if ip else f"zspr {dev}"
        path_hint = f"All relative paths (e.g. `./device/{ip}/log/`) are relative to your working directory. " if ip else ""
        return (
            f"Use {tag} as the primary guide. "
            f"The user's target device is {device_label}. "
            f"{cwd_note}"
            f"{path_hint}"
            f"Proceed directly with their request — do not ask for the device again.\n\n"
            f"{question}"
        )
    return (
        f"Use {tag} as the primary guide. "
        f"{cwd_note}"
        f"All relative paths are relative to your working directory.\n\n"
        f"{question}"
    )


def is_add_usage_example(question: str) -> bool:
    """Return True if the user is asking to add the current conversation as a usage example."""
    return bool(_USAGE_EXAMPLE_RE.search(question))


def format_conversation_as_example(messages: list[dict]) -> str:
    """Serialize conversation messages to JSON for storage. Preserves all markers."""
    import json
    cleaned = []
    for msg in messages:
        content = msg.get("content", "").strip()
        if not content:
            continue
        cleaned.append({"role": msg["role"], "content": content})
    return json.dumps(cleaned, ensure_ascii=False)
