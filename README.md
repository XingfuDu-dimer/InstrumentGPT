# Instrument GPT

Chat-style Q&A powered by Cursor IDE. Sends questions to Cursor Chat via keyboard/mouse automation and reads answers from the `answer.md` file.

## Install

```bash
pip install -r requirements.txt
```

## Usage

1. **Open Cursor IDE first** and keep the window focusable.
2. Run the Streamlit app:

```bash
streamlit run app.py
```

3. Type your question in the chat input. **During the 4-second countdown, switch to Cursor IDE (Alt+Tab)** so the paste goes into Cursor, not the browser.
4. Automation pastes the question into Cursor Chat and submits. It waits for Cursor to write the answer to `cursor_chat/answer.md`.
5. The webpage polls `answer.md` and displays the answer.

## Notes

- The Cursor window title must contain "Cursor".
- The prompt includes a system instruction asking Cursor to write its answer to `cursor_chat/answer.md`.
- If auto-focus fails, focus the Cursor window manually and try again.
- Timeout is 2 minutes. If Cursor hasn't finished, check `cursor_chat/answer.md` manually.
