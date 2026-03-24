#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.local/bin:${HOME}/.cursor/bin:${PATH}"
# Set by systemd Environment=, EnvironmentFile (.env), or default below
export INSTRUMENT_CWD="${INSTRUMENT_CWD:-${HOME}/GPT/Instrument}"
exec streamlit run app.py
