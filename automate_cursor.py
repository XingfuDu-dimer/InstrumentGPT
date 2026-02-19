"""
Automation script: Paste clipboard content into Cursor Chat and submit.
Question must be copied to clipboard before running.
"""
import time
import sys
import os

try:
    import pyautogui
    import pyperclip
    import pygetwindow as gw
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    print("Run: pip install pyautogui pyperclip pygetwindow", file=sys.stderr)
    sys.exit(1)

pyautogui.PAUSE = 0.3
pyautogui.FAILSAFE = True  # Move mouse to top-left corner to abort


def focus_cursor():
    """Find and focus Cursor IDE window (exclude browser tabs with 'Cursor' in title)."""
    # Exclude browser windows - we want Cursor IDE, not cursor.com in Chrome
    excluded = ("chrome", "edge", "firefox", "brave", "opera", "safari")
    all_wins = gw.getAllWindows()
    # 1. Prefer: Instrument project (e.g. "file.mdc - Instrument [SSH: x] - Cursor")
    wins = [w for w in all_wins if w.title.strip().lower().endswith(" - cursor") and "instrument" in w.title.lower()]
    # 2. Fallback: any title ending with " - Cursor"
    if not wins:
        wins = [w for w in all_wins if w.title.strip().lower().endswith(" - cursor")]
    # 3. Fallback: any title containing "cursor"
    if not wins:
        wins = [w for w in all_wins if "cursor" in w.title.lower()]
    # Drop browser windows
    wins = [w for w in wins if not any(ex in w.title.lower() for ex in excluded)]
    if wins:
        try:
            wins[0].activate()
            time.sleep(1.2)  # Let window manager switch focus before sending keys
            return True
        except Exception:
            pass
    return False


def _run_palette_command(cmd_text: str):
    """Run a command via palette. Assumes editor has focus."""
    pyautogui.hotkey("ctrl", "shift", "p")
    time.sleep(0.6)
    pyautogui.typewrite(cmd_text, interval=0.05)
    time.sleep(0.5)
    pyautogui.press("enter")
    time.sleep(0.8)


def _open_new_chat():
    """Open new Cursor chat via palette. Only called when new chat is triggered."""
    pyautogui.press("escape")
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "1")  # Focus editor
    time.sleep(0.3)
    _run_palette_command("new chat")


def paste_and_submit(open_chat: bool = False):
    """
    - open_chat=True: New chat via palette, then paste + Enter.
    - open_chat=False: Ctrl+L to focus existing chat input, then paste + Enter.
    """
    if open_chat:
        _open_new_chat()
    else:
        pyautogui.press("escape")
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "1")
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "l")
        time.sleep(0.8)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)
    pyautogui.press("enter")


def main():
    input_path = sys.argv[1] if len(sys.argv) > 1 else None
    open_chat = "--new-chat" in sys.argv
    if input_path and os.path.exists(input_path):
        with open(input_path, "r", encoding="utf-8") as f:
            pyperclip.copy(f.read())

    if focus_cursor():
        paste_and_submit(open_chat=open_chat)
        print("OK")
    else:
        print("Cursor window not found. Ensure Cursor is open and try focusing it manually first.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
