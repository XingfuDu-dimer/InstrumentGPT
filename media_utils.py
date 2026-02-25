"""Image, Plotly chart, and message rendering utilities."""
import glob
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent

_IMAGE_MARKER = "<!-- ATTACHED_IMAGES:"
_IMAGE_EXT_RE = re.compile(r'[\w.\-]+\.(?:png|jpg|jpeg|svg)', re.IGNORECASE)
_PLOTLY_MARKER = "<!-- PLOTLY_CHART:"


def find_new_images(cwd: str, since: float, response_text: str) -> list[str]:
    """Find images created during this request via timestamp scan + response parsing."""
    found: list[str] = []
    if not cwd or not os.path.isdir(cwd):
        return found
    seen: set[str] = set()

    for ext in ("*.png", "*.jpg", "*.jpeg", "*.svg"):
        for p in glob.glob(os.path.join(cwd, "**", ext), recursive=True):
            ap = os.path.abspath(p)
            if ap not in seen and os.path.getmtime(p) > since:
                seen.add(ap)
                found.append(ap)

    for name in _IMAGE_EXT_RE.findall(response_text):
        for p in glob.glob(os.path.join(cwd, "**", name), recursive=True):
            ap = os.path.abspath(p)
            if ap not in seen:
                seen.add(ap)
                found.append(ap)

    found.sort(key=lambda p: os.path.getmtime(p))
    return found


def attach_images(content: str, image_paths: list[str]) -> str:
    if not image_paths:
        return content
    return f"{content}\n{_IMAGE_MARKER}{'|'.join(image_paths)} -->"


def split_images(content: str) -> tuple[str, list[str]]:
    if _IMAGE_MARKER not in content:
        return content, []
    idx = content.index(_IMAGE_MARKER)
    text = content[:idx].rstrip()
    marker = content[idx:]
    paths_str = marker[len(_IMAGE_MARKER):-len(" -->")].strip()
    return text, paths_str.split("|") if paths_str else []


def try_interactive_plot(cwd: str, response_text: str):
    """Call dataAnalysisPlotly.py --plotly-json; return (cache_path, fig) or (None, None)."""
    log_match = re.search(r'(InstrumentDebug[\w\-\.]+\.log)', response_text)
    if not log_match:
        return None, None
    log_name = log_match.group(1)

    log_path = os.path.join(cwd, "log", log_name)
    if not os.path.isfile(log_path):
        matches = glob.glob(os.path.join(cwd, "**", log_name), recursive=True)
        log_path = matches[0] if matches else None
    if not log_path or not os.path.isfile(log_path):
        return None, None

    script = os.path.join(cwd, "scripts", "dataAnalysisPlotly.py")
    if not os.path.isfile(script):
        return None, None

    try:
        result = subprocess.run(
            [sys.executable, script, log_path, "--plotly-json"],
            capture_output=True, text=True, cwd=cwd, timeout=60,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None, None

        import plotly.io as pio
        fig = pio.from_json(result.stdout)

        cache_dir = ROOT / "data" / "plotly_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{int(time.time() * 1000)}.json"
        cache_file.write_text(result.stdout, encoding="utf-8")

        return str(cache_file), fig
    except Exception:
        return None, None


def attach_plotly(content: str, cache_path: str) -> str:
    return f"{content}\n{_PLOTLY_MARKER}{cache_path} -->"


def split_plotly(content: str) -> tuple[str, str | None]:
    if _PLOTLY_MARKER not in content:
        return content, None
    idx = content.index(_PLOTLY_MARKER)
    text = content[:idx].rstrip()
    marker = content[idx:]
    cache_path = marker[len(_PLOTLY_MARKER):-len(" -->")].strip()
    return text, cache_path


def render_message(content: str) -> None:
    """Render a chat message (markdown, images, Plotly charts) to Streamlit."""
    if _PLOTLY_MARKER in content:
        text, cache_path = split_plotly(content)
        st.markdown(text)
        if cache_path and os.path.isfile(cache_path):
            try:
                import plotly.io as pio
                fig = pio.from_json(Path(cache_path).read_text(encoding="utf-8"))
                st.plotly_chart(fig, use_container_width=True, key=f"plotly_{cache_path}")
            except Exception:
                pass
        return

    text, image_paths = split_images(content)
    st.markdown(text)
    for img_path in image_paths:
        if os.path.isfile(img_path):
            st.image(img_path, caption=os.path.basename(img_path))


def relative_time(ts: float) -> str:
    diff = time.time() - ts
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff / 60)}m ago"
    if diff < 86400:
        return f"{int(diff / 3600)}h ago"
    return datetime.fromtimestamp(ts).strftime("%m/%d")
