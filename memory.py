"""Structured conversation memory for device diagnostic sessions.

Three-tier memory system:
  1. DiagnosticState  — structured task state machine (JSON in DB)
  2. Rolling summary  — compressed older turns (text in DB)
  3. Recent turns     — last N raw exchanges (filtered on the fly)

Replaces the naive "dump all history" approach with a compact,
structured prompt that keeps token usage roughly constant regardless
of conversation length.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

# ── Content filters ──────────────────────────────────────────────────────────

_MARKER_RE = re.compile(r"<!-- (?:PLOTLY_CHART|ATTACHED_IMAGES):.*?-->", re.DOTALL)
_LOG_LINE_BLOCK_RE = re.compile(
    r"(?:^|\n)(?:\[?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}.*?\n){5,}",
    re.DOTALL,
)
_LONG_CODE_RE = re.compile(r"```[^\n]*\n.{2000,}?```", re.DOTALL)

RECENT_TURN_COUNT = 3
MAX_SUMMARY_CHARS = 5000
MAX_RECENT_MSG_CHARS = 3000


def filter_content(content: str) -> str:
    """Strip UI markers, raw log dumps, and oversized code blocks."""
    text = _MARKER_RE.sub("", content)
    text = _LOG_LINE_BLOCK_RE.sub("\n[raw log omitted — see diagnostic_context]\n", text)
    text = _LONG_CODE_RE.sub("```\n[large code block omitted]\n```", text)
    return text.strip()


def compress_message(role: str, content: str, max_chars: int = 400) -> str:
    """Compress a single message for the rolling summary."""
    filtered = filter_content(content)
    if len(filtered) <= max_chars:
        return f"{role}: {filtered}"

    if role == "Assistant":
        paragraphs = [p.strip() for p in filtered.split("\n\n") if p.strip()]
        if len(paragraphs) >= 2:
            compressed = paragraphs[0] + "\n...\n" + paragraphs[-1]
            if len(compressed) <= max_chars:
                return f"{role}: {compressed}"
        return f"{role}: {filtered[:max_chars]}…"
    else:
        return f"{role}: {filtered[:max_chars]}…"


# ── Rolling summary ──────────────────────────────────────────────────────────

def build_summary(
    existing_summary: str,
    turns_to_compress: list[dict],
) -> str:
    """Compress evicted turns into the rolling summary.

    Each turn is a dict with 'role' and 'content'.
    The summary is capped at MAX_SUMMARY_CHARS; when exceeded, the oldest
    portion is trimmed (decay).
    """
    new_parts = []
    for msg in turns_to_compress:
        role = "User" if msg["role"] == "user" else "Assistant"
        new_parts.append(compress_message(role, msg["content"]))
    new_block = "\n".join(new_parts)

    if existing_summary:
        combined = existing_summary + "\n" + new_block
    else:
        combined = new_block

    if len(combined) > MAX_SUMMARY_CHARS:
        combined = "…" + combined[-(MAX_SUMMARY_CHARS - 1):]

    return combined


# ── Diagnostic state ─────────────────────────────────────────────────────────

@dataclass
class DiagnosticState:
    """Structured state machine for an ongoing diagnostic session."""

    device_ip: Optional[str] = None
    device_name: Optional[str] = None
    last_log_file: Optional[str] = None
    downloaded_logs: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    root_causes: list[str] = field(default_factory=list)
    status: str = "idle"  # idle | investigating | resolved

    # ── Serialization ────────────────────────────────────────────────────

    def serialize(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def deserialize(cls, raw: str) -> DiagnosticState:
        if not raw:
            return cls()
        try:
            return cls(**json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return cls()

    # ── Prompt rendering ─────────────────────────────────────────────────

    def to_prompt_block(self) -> str:
        if self.status == "idle":
            return ""
        lines = [f"Device: {self.device_name} ({self.device_ip})"]
        if self.last_log_file:
            lines.append(f"Last log: {self.last_log_file}")
        if self.downloaded_logs:
            lines.append(f"Downloaded: {', '.join(self.downloaded_logs[-3:])}")
        if self.findings:
            lines.append("Key findings:")
            for f in self.findings[-5:]:
                lines.append(f"  - {f}")
        if self.hypotheses:
            lines.append("Active hypotheses:")
            for h in self.hypotheses[-3:]:
                lines.append(f"  - {h}")
        if self.root_causes:
            lines.append("Confirmed root causes:")
            for rc in self.root_causes:
                lines.append(f"  - {rc}")
        return "\n".join(lines)


# ── State extraction (heuristic) ─────────────────────────────────────────────

_LOG_FILE_RE = re.compile(r"(Instrument\w+_\d{4}-\d{2}-\d{2}_[\d\-]+(?:\.\d+)?\.log)")

_ROOT_CAUSE_RE = re.compile(
    r"(?:Root [Cc]ause|Confirmed|Resolved)\s*[:：]\s*(.+?)(?:\n|$)"
)
_HYPOTHESIS_RE = re.compile(
    r"(?:Hypothesis|Possible cause|Suspect|May be caused by|Likely)\s*[:：]\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_FINDING_RE = re.compile(
    r"^[ \t]*[-•]\s+\*\*(.+?)\*\*",
    re.MULTILINE,
)


def extract_state_updates(
    response: str,
    state: DiagnosticState,
) -> DiagnosticState:
    """Parse the assistant response and update the diagnostic state."""

    for m in _LOG_FILE_RE.finditer(response):
        log_name = m.group(1)
        if log_name not in state.downloaded_logs:
            state.downloaded_logs.append(log_name)
            state.last_log_file = log_name

    for m in _ROOT_CAUSE_RE.finditer(response):
        rc = m.group(1).strip().rstrip(".")
        if 20 < len(rc) < 200 and rc not in state.root_causes:
            state.root_causes.append(rc)

    for m in _HYPOTHESIS_RE.finditer(response):
        hyp = m.group(1).strip().rstrip(".")
        if 10 < len(hyp) < 200 and hyp not in state.hypotheses:
            state.hypotheses.append(hyp)

    for m in _FINDING_RE.finditer(response):
        finding = m.group(1).strip().rstrip(".")
        if 10 < len(finding) < 200 and finding not in state.findings:
            state.findings.append(finding)

    if state.device_ip and state.status == "idle":
        state.status = "investigating"
    if state.root_causes and state.status == "investigating":
        state.status = "resolved"

    return state


# ── Prompt builder ───────────────────────────────────────────────────────────

def build_prompt(
    current_question: str,
    all_messages: list[dict],
    diagnostic_state: DiagnosticState,
    existing_summary: str,
    is_device_query: bool,
) -> tuple[str, str]:
    """Assemble a structured prompt from memory layers.

    Returns (prompt_text, updated_summary).
    """

    # --- Partition into summary-zone and recent-zone ---
    recent_count = RECENT_TURN_COUNT * 2  # N exchanges = 2N messages
    if len(all_messages) > recent_count:
        older = all_messages[:-recent_count]
        recent = all_messages[-recent_count:]
        updated_summary = build_summary("", older)
    else:
        recent = list(all_messages)
        updated_summary = existing_summary or ""

    # --- Assemble blocks ---
    blocks: list[str] = [current_question]

    # Diagnostic state
    state_block = diagnostic_state.to_prompt_block()
    if state_block:
        blocks.append(f"<diagnostic_context>\n{state_block}\n</diagnostic_context>")

    # Behavior note
    if is_device_query:
        blocks.append(
            "<note>Use diagnostic_context and conversation_summary to avoid "
            "re-downloading logs already analyzed. Reuse existing findings. "
            "If the user asks for fresh logs, re-download.</note>"
        )
    elif any(m["role"] == "assistant" for m in all_messages):
        blocks.append(
            "<note>Current question is NOT about device debugging. "
            "Do not download logs or analyze devices. Answer directly.</note>"
        )

    # Summary of older turns
    if updated_summary:
        blocks.append(
            f"<conversation_summary>\n{updated_summary}\n</conversation_summary>"
        )

    # Recent raw turns (filtered)
    if recent:
        recent_lines: list[str] = []
        for msg in recent:
            role = "User" if msg["role"] == "user" else "Assistant"
            content = filter_content(msg["content"])
            if len(content) > MAX_RECENT_MSG_CHARS:
                half = MAX_RECENT_MSG_CHARS // 2
                content = content[:half] + "\n[...]\n" + content[-half:]
            recent_lines.append(f"{role}: {content}")
        recent_block = "\n\n".join(recent_lines)
        blocks.append(
            f"<recent_conversation>\n{recent_block}\n</recent_conversation>"
        )

    return "\n\n".join(blocks), updated_summary
