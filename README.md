# Instrument GPT

**Instrument GPT** is the tool (this repo). Your **instrument repo** (or any project you work on) is the real working directory — that’s where the Cursor agent runs (reads files, runs commands, etc.).

This app gives you a ChatGPT-like UI to talk to the Cursor Agent CLI: per-user conversation history, streaming responses, and sidebar conversation management. The agent’s **working directory** should point at your instrument project, not at Instrument GPT.

## Prerequisites

1. **Cursor CLI** — install and authenticate:

```powershell
# Windows PowerShell
irm 'https://cursor.com/install?win32=true' | iex
```

```bash
# macOS / Linux / WSL
curl https://cursor.com/install -fsS | bash
```

Then log in (once):

```bash
agent login
```

2. **Python 3.11+**

## Install

```bash
pip install -r requirements.txt
```

## Usage

Set the **default working directory** to your **Instrument directory** (the real repo the agent should run in). Instrument GPT lives elsewhere; the agent’s cwd should be your project.

- **`INSTRUMENT_CWD`** — set this to your instrument (or target) repo path when you start the app. That becomes the default "Working Directory" in the app.
- If unset or invalid, it falls back to the Instrument GPT app directory (only useful for trying the app; for real use, set your instrument path).

```powershell
# Windows PowerShell — replace with your real project path
$env:INSTRUMENT_CWD = "C:\Users\You\Desktop\YourProject"
streamlit run app.py
```

```bash
# macOS / Linux / WSL — replace with your real project path
INSTRUMENT_CWD=/home/you/YourProject streamlit run app.py
```

Then open the URL shown (default `http://localhost:8501`). You can change **Working Directory**, model, mode, and MDC tag in the sidebar **Settings** anytime.

### "Agent not found" in the app (but `agent` works in terminal)

If you already ran `agent login` in a terminal and `agent` works there, the app may not see it because Streamlit was started with a different PATH (e.g. from VS Code or another terminal). Set the **full path** to the agent executable:

1. In a terminal where `agent` works, get its path:
   - **PowerShell:** `(Get-Command agent).Source`
   - **CMD:** `where agent`
2. Before starting Streamlit, set it (same session):
   - **PowerShell:** `$env:INSTRUMENT_AGENT_PATH = "C:\path\to\agent.exe"`
   - **Bash:** `export INSTRUMENT_AGENT_PATH=/path/to/agent`
3. Run `streamlit run app.py` in that same terminal.

The app also looks for `agent.exe` in `%USERPROFILE%\.cursor\bin` and under `%LOCALAPPDATA%` on Windows; if your install is there, it may be found without the env var.

## Features

- **Streaming responses** — text streams in via `agent -p --output-format stream-json`
- **Per-IP conversation history** — SQLite-backed; each client gets isolated conversations
- **Conversation list** — sidebar shows all conversations; click to switch, **×** to delete
- **Session resume** — follow-up messages in a conversation use `--resume` for CLI context
- **Configurable** — model, mode (agent/ask/plan), MDC tag, working directory in sidebar Settings

## Project structure

| Path | Description |
|------|-------------|
| `app.py` | Streamlit UI (sidebar + chat) |
| `cursor_cli.py` | Cursor CLI wrapper, NDJSON stream parsing |
| `db.py` | SQLite schema and access for conversations & messages |
| `requirements.txt` | Python dependencies |
| `data/` | Auto-created; contains `conversations.db` |
