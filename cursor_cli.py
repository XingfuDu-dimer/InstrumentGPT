"""Cursor CLI wrapper with NDJSON streaming support.

Uses `agent -p --output-format stream-json --stream-partial-output` to stream
individual text deltas from the Cursor Agent CLI.

Stream-json event types (NDJSON, one JSON object per line):
  type=user        — echoed user prompt
  type=assistant   — text delta  (message.content[].text)
  type=tool_call   — tool started/completed
  type=result      — final summary with duration_ms

To fix "agent not found" when PATH differs (e.g. Streamlit started from IDE):
  Set INSTRUMENT_AGENT_PATH to the full path to the agent executable.

Debug logging:
  Set env INSTRUMENT_DEBUG_NDJSON=1 to write raw NDJSON lines to
  data/debug_ndjson/<timestamp>.jsonl for protocol analysis.
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Generator

_ROOT = Path(__file__).resolve().parent


def _open_debug_log():
    """Open a debug NDJSON log file if INSTRUMENT_DEBUG_NDJSON is set."""
    if not os.environ.get("INSTRUMENT_DEBUG_NDJSON"):
        return None
    log_dir = _ROOT / "data" / "debug_ndjson"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{int(time.time())}.jsonl"
    return open(log_path, "w", encoding="utf-8")


def _find_agent_cmd() -> str:
    # Explicit path wins (for when Streamlit's PATH doesn't include agent)
    explicit = os.environ.get("INSTRUMENT_AGENT_PATH")
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p.resolve())
        if p.is_dir():
            for name in ("agent", "agent.exe", "cursor-agent", "cursor-agent.exe"):
                candidate = p / name
                if candidate.is_file():
                    return str(candidate.resolve())

    for cmd in ("agent", "cursor-agent"):
        found = shutil.which(cmd)
        if found:
            return found

    # Windows: common install locations (install script may not be on Streamlit's PATH)
    if os.name == "nt":
        for base in (
            os.environ.get("USERPROFILE", ""),
            os.environ.get("LOCALAPPDATA", ""),
        ):
            if not base:
                continue
            for subdir, exe in (
                (".cursor/bin", "agent.exe"),
                ("AppData/Local/cursor-agent", "agent.exe"),
                ("cursor-agent", "agent.exe"),
            ):
                candidate = Path(base) / subdir / exe
                if candidate.is_file():
                    return str(candidate.resolve())

    return "agent"  # fallback for error message


_NOT_FOUND_MSG = (
    "Cursor CLI (`agent`) not found.\n\n"
    "If you already installed and logged in in another terminal, set the full path "
    "so this app can find it (e.g. in the same terminal before running Streamlit):\n\n"
    "**PowerShell:** `$env:INSTRUMENT_AGENT_PATH = \"C:\\path\\to\\agent.exe\"`\n\n"
    "To find where `agent` is: in a terminal where `agent` works, run "
    "`(Get-Command agent).Source` (PowerShell) or `where agent` (CMD).\n\n"
    "Otherwise install: `irm 'https://cursor.com/install?win32=true' | iex` then `agent login`."
)


def create_process(
    prompt: str,
    cwd: str | Path | None = None,
    model: str | None = None,
    mode: str = "agent",
    resume_session: str | None = None,
) -> tuple[subprocess.Popen | None, str | None]:
    """Start a Cursor CLI subprocess.

    Returns (process, None) on success or (None, error_message) on failure.
    """
    agent = _find_agent_cmd()
    args = [
        agent, "-p",
        "--output-format", "stream-json",
        "--stream-partial-output",
        "-f",
    ]
    if model:
        args.extend(["-m", model])
    if mode and mode != "agent":
        args.extend(["--mode", mode])
    if resume_session:
        args.extend(["--resume", resume_session])

    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=str(cwd) if cwd else None,
        )
        process.stdin.write(prompt)
        process.stdin.close()
        return process, None
    except FileNotFoundError:
        return None, _NOT_FOUND_MSG


def kill_process(process: subprocess.Popen | None) -> None:
    """Terminate a CLI subprocess if it is still running."""
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def iter_events(
    process: subprocess.Popen,
) -> Generator[tuple[str, str], None, None]:
    """Iterate NDJSON events from a running CLI process.

    Yields (event_type, payload) tuples:
        ("text",         "<chunk>")   — assistant text delta (append)
        ("text_replace", "<full>")    — complete text replacing accumulated
        ("tool",         "<desc>")    — tool activity indicator
        ("session_id",   "<id>")      — CLI session id (for --resume)
        ("error",        "<message>") — error detail
        ("done",         "<exit>")    — stream finished

    The CLI with --stream-partial-output sends a mix of:
      - Small deltas during streaming
      - Cumulative full-text events (text that startswith accumulated)
      - A final complete message at the end
    We handle all three and deduplicate automatically.
    """
    session_id: str | None = None
    accumulated = ""
    dbg = _open_debug_log()
    try:
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            if dbg:
                dbg.write(line + "\n")
                dbg.flush()

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not session_id and "session_id" in data:
                session_id = data["session_id"]
                yield ("session_id", session_id)

            evt = data.get("type", "")

            if evt == "assistant":
                for item in data.get("message", {}).get("content", []):
                    text = item.get("text", "")
                    if item.get("type") != "text" or not text:
                        continue

                    if not accumulated:
                        accumulated = text
                        yield ("text", text)
                    elif text.startswith(accumulated):
                        # Cumulative event — extract the new tail
                        new_part = text[len(accumulated):]
                        if new_part:
                            accumulated = text
                            yield ("text", new_part)
                    elif accumulated.startswith(text) or text in accumulated:
                        pass  # subset of what we already have
                    elif len(text) >= len(accumulated) * 0.5 and len(text) > 100:
                        # Final complete re-send (possibly cleaner than
                        # the streamed version). Replace.
                        accumulated = text
                        yield ("text_replace", text)
                    else:
                        # Genuine new delta
                        accumulated += text
                        yield ("text", text)

            elif evt == "tool_call" and data.get("subtype") == "started":
                desc = _describe_tool_call(data.get("tool_call", {}))
                if desc:
                    yield ("tool", desc)

        process.wait()
        if process.returncode and process.returncode != 0:
            stderr = process.stderr.read().strip()
            if stderr:
                yield ("error", stderr)

    except Exception as exc:
        yield ("error", str(exc))
    finally:
        kill_process(process)
        if dbg:
            dbg.close()

    rc = process.returncode if process.returncode is not None else 1
    yield ("done", str(rc))


def stream_response(
    prompt: str,
    cwd: str | Path | None = None,
    model: str | None = None,
    mode: str = "agent",
    resume_session: str | None = None,
) -> Generator[tuple[str, str], None, None]:
    """Convenience wrapper: create_process + iter_events."""
    process, err = create_process(prompt, cwd, model, mode, resume_session)
    if err:
        yield ("error", err)
        yield ("done", "1")
        return
    yield from iter_events(process)


# ---------------------------------------------------------------------------
# Tool-call descriptions for the streaming UI
# ---------------------------------------------------------------------------
_TOOL_MAP: list[tuple[str, str, str]] = [
    ("shellToolCall",  "command", "Running"),
    ("readToolCall",   "path",    "Reading"),
    ("editToolCall",   "path",    "Editing"),
    ("writeToolCall",  "path",    "Writing"),
    ("deleteToolCall", "path",    "Deleting"),
    ("grepToolCall",   "pattern", "Searching"),
    ("globToolCall",   "globPattern", "Finding"),
    ("lsToolCall",     "path",    "Listing"),
]


def _describe_tool_call(tc: dict) -> str:
    for key, arg_field, verb in _TOOL_MAP:
        if key in tc:
            val = tc[key].get("args", {}).get(arg_field, "")
            if val:
                short = val if len(val) <= 80 else val[:77] + "..."
                return f"{verb}: `{short}`"
            return ""
    return ""
