# Memory Architecture Refactor

## Problem

Current design sends the entire raw conversation history (up to 50K chars) on every turn. This wastes tokens, resends raw logs/UI markers, and degrades reasoning as conversations grow.

## Proposed Architecture

```
┌─────────────────────────────────────────────┐
│              Final Prompt (to CLI)           │
│                                              │
│  1. <current_request>      ← raw user input  │
│  2. <diagnostic_context>   ← structured JSON  │
│  3. <conversation_summary> ← compressed       │
│  4. <recent_turns>         ← last N raw       │
└─────────────────────────────────────────────┘
         ▲           ▲            ▲
         │           │            │
    DiagState    Summarizer    Filter
         │           │            │
         └───── SQLite DB ────────┘
```

### Token Budget (estimated)

| Conversation length | Current | Proposed |
|---------------------|---------|----------|
| Turn 1              | ~2K     | ~2K      |
| Turn 5              | ~25K    | ~6K      |
| Turn 10             | ~50K (cap) | ~8K   |

---

## Memory Layers

### Layer 1: Current Request

The user's raw input, enriched with MDC tag if an IP is detected. Always placed first in the prompt so the agent sees it immediately.

### Layer 2: Diagnostic State (structured)

A persistent JSON object tracking the current diagnostic session:

```python
@dataclass
class DiagnosticState:
    device_ip: str | None         # e.g. "10.1.1.47"
    device_name: str | None       # e.g. "zspr 052"
    last_log_file: str | None     # e.g. "InstrumentDebug_2026-02-19_23-24-45.39.log"
    downloaded_logs: list[str]    # all logs downloaded in this conversation
    findings: list[str]           # key diagnostic findings
    hypotheses: list[str]         # active hypotheses being tested
    root_causes: list[str]        # confirmed root causes
    status: str                   # idle | investigating | resolved
```

Serialized as JSON in a new `diagnostic_state` column on `conversations` table. Updated after each assistant response via heuristic extraction (regex for log filenames, root cause mentions, etc.).

Rendered in prompt as:

```
<diagnostic_context>
Device: zspr 052 (10.1.1.47)
Last log: InstrumentDebug_2026-02-19_23-24-45.39.log
Key findings:
  - PID controller stable, PI-dominant (kd ≈ 0)
  - Temperature oscillation ±0.15°C within normal band
</diagnostic_context>
```

### Layer 3: Conversation Summary (compressed)

A rolling text summary of older turns. Stored in a new `summary` column on `conversations` table.

**How it works:**
- Keep the last N exchanges (default 3) as raw text in `<recent_turns>`
- When total messages exceed 2N, the oldest messages get evicted from "recent" and compressed into the summary
- Compression = strip UI markers + strip raw log dumps + keep first/last paragraphs of assistant responses + cap per-message length

**What gets stripped:**
- `<!-- PLOTLY_CHART:... -->` markers
- `<!-- ATTACHED_IMAGES:... -->` markers
- Raw log blocks (lines with `[YYYY-MM-DD HH:MM:SS]` patterns)
- Code blocks > 2000 chars

**What gets kept:**
- User questions (truncated to ~300 chars)
- Assistant conclusions/summaries (first + last paragraph, ~300 chars)

### Layer 4: Recent Turns (filtered raw)

Last N exchanges (default 3), with content filtering applied:
- UI markers stripped
- Each message capped at ~3000 chars (keep first 1500 + last 1500)
- Raw logs replaced with `[raw log — see findings above]`

---

## Module Structure

```
InstrumentGPT/
├── app.py           # UI — replace build_context_prompt() call
├── cursor_cli.py    # CLI wrapper — unchanged
├── db.py            # Add summary + diagnostic_state columns
├── memory.py        # NEW — all memory logic
```

### memory.py Functions

| Function | Purpose |
|----------|---------|
| `filter_content(text)` | Strip markers, log dumps, oversized code blocks |
| `compress_message(role, content)` | Compress a single message for summary |
| `build_summary(existing, turns_to_evict)` | Update rolling summary with newly evicted turns |
| `extract_state_updates(response, state)` | Parse assistant response → update DiagnosticState |
| `build_prompt(question, messages, state, summary, is_device)` | Assemble the final structured prompt |

---

## Prompt Output Format

```
10.1.1.47 為什麼溫度波動？

<diagnostic_context>
Device: zspr 052 (10.1.1.47)
Last log: InstrumentDebug_2026-02-19_23-24-45.39.log
Downloaded: InstrumentDebug_2026-02-19_23-24-45.39.log
Key findings:
  - PID controller stable, PI-dominant (kd ≈ 0)
  - Temperature oscillation ±0.15°C within normal band
</diagnostic_context>

<note>Use diagnostic_context and conversation_summary to avoid
re-downloading logs already analyzed. Reuse existing findings.</note>

<conversation_summary>
User: 10.1.1.47 plot temp control
Assistant: Downloaded InstrumentDebug_2026-02-19_23-24-45.39.log.
PID controller maintaining setpoints with small oscillation. Power
at ~45% for IDs 1-4.
...
Temperature control healthy — no instability or divergence.
</conversation_summary>

<recent_conversation>
User: 那 kp 可以調高嗎？
Assistant: 目前 kp ~0.09，可以適度調高到 0.12-0.15 ...
</recent_conversation>
```

---

## DB Schema Changes

Add two nullable columns to `conversations` (backward-compatible):

```sql
ALTER TABLE conversations ADD COLUMN summary TEXT DEFAULT '';
ALTER TABLE conversations ADD COLUMN diagnostic_state TEXT DEFAULT '';
```

New helper functions in `db.py`:

```python
def get_memory(conversation_id: str) -> tuple[str, str]:
    """Return (summary, diagnostic_state_json)."""

def update_memory(conversation_id: str, summary: str, diagnostic_state: str):
    """Persist updated summary and diagnostic state."""
```

---

## app.py Integration

Replace the current `build_context_prompt()` call:

```python
# BEFORE CLI call:
summary, state_json = db.get_memory(conv_id)
diag_state = DiagnosticState.deserialize(state_json)

if ip_result := _extract_ip(prompt):
    diag_state.device_ip = ip_result[0]
    diag_state.device_name = f"zspr {ip_result[1]}"

enriched, updated_summary = memory.build_prompt(
    current_question=enriched,
    all_messages=messages,
    diagnostic_state=diag_state,
    existing_summary=summary,
    is_device_query=_has_device(prompt),
)

# AFTER CLI response:
diag_state = memory.extract_state_updates(full_response, diag_state)
db.update_memory(conv_id, updated_summary, diag_state.serialize())
```

---

## Migration Strategy

### Phase 1: Add without breaking (safe)

- Create `memory.py` with all functions
- Add DB columns with `try/except ALTER TABLE`
- Add `db.get_memory()` / `db.update_memory()`
- Do NOT change the prompt building yet

### Phase 2: Swap prompt builder

- Replace `build_context_prompt()` call with `memory.build_prompt()`
- Add `extract_state_updates()` after each response
- Old conversations work fine — empty summary/state = falls back to raw recent turns

### Phase 3: Enhancements (optional, later)

- LLM-based summarization (background CLI call to generate better summaries)
- Memory inspector UI panel in sidebar showing current DiagnosticState
- Memory pruning for very old conversations
- Configurable RECENT_TURN_COUNT in Settings

---

## What Does NOT Change

- `cursor_cli.py` — untouched
- `enrich_prompt()` — still works the same
- SQLite `messages` table — all raw messages still stored
- Plotly/image detection — still works, markers only stripped from history context
- The UI — completely unchanged
