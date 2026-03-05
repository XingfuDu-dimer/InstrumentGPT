"""Image, Plotly chart, and message rendering utilities."""
import glob
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent

_IMAGE_MARKER = "<!-- ATTACHED_IMAGES:"
_IMAGE_EXT_RE = re.compile(r'[\w.\-]+\.(?:png|jpg|jpeg|svg)', re.IGNORECASE)
_PLOTLY_MARKER = "<!-- PLOTLY_CHART:"

# Explicit marker: <!-- PLOTLY: path/to/file.json -->
_PLOTLY_MARKER_RE = re.compile(r'<!--\s*PLOTLY\s*:\s*([^\s>]+)\s*-->', re.IGNORECASE)
# Generic paths: path/to/file.json or path/to/file.html (at least one path segment)
_PLOTLY_PATH_RE = re.compile(
    r'(?:^|[\s"\'(\[])((?:\./|[a-zA-Z0-9_\-]+/)[a-zA-Z0-9_./\-]*\.(?:json|html))',
    re.IGNORECASE,
)


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


def _load_plotly_from_json(path: str):
    """Load Plotly figure from JSON file. Returns fig or None."""
    try:
        import plotly.io as pio
        return pio.read_json(path)
    except Exception:
        return None


def _load_plotly_from_html(path: str):
    """Load Plotly figure from HTML file (extract embedded JSON). Returns fig or None."""
    try:
        import plotly.io as pio
        html = Path(path).read_text(encoding="utf-8", errors="ignore")
        # Plotly HTML embeds data in Plotly.newPlot(div, data, layout, config)
        matches = re.findall(r"Plotly\.newPlot\((.*)\)", html[-65536:])
        if not matches:
            return None
        call_args = json.loads(f"[{matches[0]}]")
        plotly_json = json.dumps({"data": call_args[1], "layout": call_args[2]})
        return pio.from_json(plotly_json)
    except Exception:
        return None


def try_interactive_plot(cwd: str, response_text: str):
    """
    Parse response for Plotly file paths (generic, repo-agnostic).
    Supports: <!-- PLOTLY: path --> or any path/to/file.json|.html in the text.
    Returns (cache_path, fig) or (None, None).
    """
    if not cwd or not os.path.isdir(cwd):
        return None, None

    candidates = []

    # Explicit marker: <!-- PLOTLY: path -->
    for m in _PLOTLY_MARKER_RE.finditer(response_text):
        candidates.append(m.group(1).strip())

    # Generic paths in text (e.g. "saved to device/10.1.1.46/log/foo.html")
    for m in _PLOTLY_PATH_RE.finditer(response_text):
        candidates.append(m.group(1).strip())

    for rel_path in candidates:
        if not rel_path or ".." in rel_path:
            continue
        full_path = os.path.join(cwd, rel_path.lstrip("./"))
        if not os.path.isfile(full_path):
            continue

        fig = None
        if full_path.lower().endswith(".json"):
            fig = _load_plotly_from_json(full_path)
        elif full_path.lower().endswith(".html"):
            fig = _load_plotly_from_html(full_path)

        if fig is not None:
            cache_dir = ROOT / "data" / "plotly_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / f"{int(time.time() * 1000)}.json"
            try:
                import plotly.io as pio
                cache_file.write_text(pio.to_json(fig), encoding="utf-8")
                return str(cache_file), fig
            except Exception:
                return str(cache_file), fig  # still return fig
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
