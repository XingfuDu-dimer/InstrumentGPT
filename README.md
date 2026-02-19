# Instrument GPT

Chat-style Q&A powered by the Cursor Agent CLI. Features a ChatGPT-like UI with per-user conversation history, streaming responses, and sidebar conversation management.

## Prerequisites

1. **Cursor CLI** — install and authenticate:

```powershell
# Windows PowerShell
irm 'https://cursor.com/install?win32=true' | iex

# macOS / Linux / WSL
curl https://cursor.com/install -fsS | bash
```

```bash
agent login
```

2. **Python 3.11+**

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
streamlit run app.py
```

Open the URL shown in the terminal (default `http://localhost:8501`). Type a question and the response streams in real-time from the Cursor Agent CLI.

## Features

- **Streaming responses** — text arrives incrementally via `agent -p --output-format stream-json`
- **Per-IP conversation history** — each user gets isolated conversations stored in SQLite
- **Conversation switching** — sidebar lists all conversations; click to switch, `×` to delete
- **Session resume** — subsequent messages in a conversation use `--resume` to maintain CLI context
- **Configurable** — model, agent mode (agent/ask/plan), MDC tag, and working directory adjustable in the sidebar settings

## Project Structure

```
app.py            Streamlit UI (sidebar + chat)
cursor_cli.py     Cursor CLI wrapper with NDJSON stream parsing
db.py             SQLite database for conversations & messages
requirements.txt  Python dependencies
data/             Auto-created; holds conversations.db
```
