"""Prompt building and device detection utilities."""
import re
_IP_PATTERN = re.compile(r"10\.1\.1\.(4[5-9]|50)(?!\d)")
_OCTET_TO_DEV = {
    "45": "050", "46": "051", "47": "052",
    "48": "053", "49": "054", "50": "055",
}
_DEVICE_NUM_RE = re.compile(r"\b(?:zspr\s*)?0?(5[0-5])\b", re.IGNORECASE)


def extract_ip(question: str) -> tuple[str, str] | None:
    """Match 10.1.1.xx in the current message only. Returns (ip, device) or None."""
    m = _IP_PATTERN.search(question)
    if not m:
        return None
    octet = m.group(1)
    dev = _OCTET_TO_DEV.get(octet, f"0{octet}")
    return f"10.1.1.{octet}", dev


def has_device(question: str) -> bool:
    """True if the question references a device by IP or number (50-55, zspr)."""
    return extract_ip(question) is not None or bool(_DEVICE_NUM_RE.search(question))


def auto_title(question: str) -> str:
    title = question.strip().split("\n")[0]
    return (title[:47] + "...") if len(title) > 50 else (title or "New Chat")


def enrich_prompt(question: str, mdc_tag: str, cwd: str = "") -> str:
    tag = mdc_tag.strip()
    if not tag or tag in question:
        return question
    cwd_note = f"Your working directory is `{cwd}`. " if cwd else ""
    result = extract_ip(question)
    if result:
        ip, dev = result
        return (
            f"Use {tag} as the primary guide. "
            f"The user's target device is zspr {dev} ({ip}). "
            f"{cwd_note}"
            f"All relative paths (e.g. `./device/{ip}/log/`) are relative to your working directory. "
            f"Proceed directly with their request — do not ask for the device again.\n\n"
            f"{question}"
        )
    return (
        f"Use {tag} as the primary guide. "
        f"{cwd_note}"
        f"All relative paths are relative to your working directory.\n\n"
        f"{question}"
    )


