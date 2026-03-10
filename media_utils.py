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
_FILE_MARKER = "<!-- ATTACHED_FILES:"
_IMAGE_EXT_RE = re.compile(r'[\w.\-]+\.(?:png|jpg|jpeg|svg)', re.IGNORECASE)
_PLOTLY_MARKER = "<!-- PLOTLY_CHART:"
_PLOTLY_HTML_MARKER = "<!-- PLOTLY_HTML:"

# Explicit marker: <!-- PLOTLY: path/to/file.json -->
_PLOTLY_MARKER_RE = re.compile(r'<!--\s*PLOTLY\s*:\s*([^\s>]+)\s*-->', re.IGNORECASE)
# Generic paths: path/to/file.json or path\to\file.html (forward or backslash)
# Allow preceding: space, newline, " ' ( [ ` > : (markdown/code context)
_PLOTLY_PATH_RE = re.compile(
    r'(?:^|[\s"\'(\[`>:])((?:\./|[a-zA-Z0-9_\-]+[/\\])[a-zA-Z0-9_./\\\-]*\.(?:json|html))',
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


def attach_files(content: str, file_paths: list[str]) -> str:
    if not file_paths:
        return content
    return f"{content}\n{_FILE_MARKER}{'|'.join(file_paths)} -->"


def split_files(content: str) -> tuple[str, list[str]]:
    if _FILE_MARKER not in content:
        return content, []
    idx = content.index(_FILE_MARKER)
    text = content[:idx].rstrip()
    marker = content[idx:]
    end = marker.find(" -->")
    paths_str = marker[len(_FILE_MARKER):end].strip() if end >= 0 else ""
    return text, paths_str.split("|") if paths_str else []


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
        matches = re.findall(r"Plotly\.(?:newPlot|react)\s*\((.*)\)", html[-65536:])
        if not matches:
            return None
        call_args = json.loads(f"[{matches[0]}]")
        plotly_json = json.dumps({"data": call_args[1], "layout": call_args[2]})
        return pio.from_json(plotly_json)
    except Exception:
        return None


def try_interactive_plot(cwd: str, response_text: str, since: float = 0):
    """
    Find Plotly figures via response text paths OR newly created JSON files.
    Returns (cache_path, fig, html_path).
    - If fig: use st.plotly_chart(fig)
    - Elif html_path: embed HTML with st.components.v1.html() (fallback when parse fails)
    """
    if not cwd or not os.path.isdir(cwd):
        return None, None, None

    candidates = []
    for m in _PLOTLY_MARKER_RE.finditer(response_text):
        candidates.append(m.group(1).strip())
    for m in _PLOTLY_PATH_RE.finditer(response_text):
        candidates.append(m.group(1).strip().strip("`"))

    if since:
        for p in glob.glob(os.path.join(cwd, "device", "*", "log", "*.json"), recursive=False):
            if os.path.getmtime(p) > since:
                rel = os.path.relpath(p, cwd)
                if rel not in candidates:
                    candidates.append(rel)

    for rel_path in candidates:
        if not rel_path or ".." in rel_path:
            continue
        rel_path_norm = rel_path.replace("\\", "/").lstrip("./")
        full_path = os.path.normpath(os.path.join(cwd, rel_path_norm))
        if not os.path.isfile(full_path):
            continue

        if full_path.lower().endswith(".json"):
            fig = _load_plotly_from_json(full_path)
            if fig is not None:
                cache_dir = ROOT / "data" / "plotly_cache"
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_file = cache_dir / f"{int(time.time() * 1000)}.json"
                try:
                    import plotly.io as pio
                    cache_file.write_text(pio.to_json(fig), encoding="utf-8")
                except Exception:
                    pass
                return str(cache_file), fig, None
        elif full_path.lower().endswith(".html"):
            fig = _load_plotly_from_html(full_path)
            if fig is not None:
                cache_dir = ROOT / "data" / "plotly_cache"
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_file = cache_dir / f"{int(time.time() * 1000)}.json"
                try:
                    import plotly.io as pio
                    cache_file.write_text(pio.to_json(fig), encoding="utf-8")
                except Exception:
                    pass
                return str(cache_file), fig, None
            return full_path, None, full_path
    return None, None, None


def attach_plotly(content: str, cache_path: str) -> str:
    return f"{content}\n{_PLOTLY_MARKER}{cache_path} -->"


def attach_plotly_html(content: str, html_path: str) -> str:
    return f"{content}\n{_PLOTLY_HTML_MARKER}{html_path} -->"


def split_plotly(content: str) -> tuple[str, str | None]:
    if _PLOTLY_MARKER not in content:
        return content, None
    idx = content.index(_PLOTLY_MARKER)
    text = content[:idx].rstrip()
    marker = content[idx:]
    cache_path = marker[len(_PLOTLY_MARKER):-len(" -->")].strip()
    return text, cache_path


def split_plotly_html(content: str) -> tuple[str, str | None]:
    if _PLOTLY_HTML_MARKER not in content:
        return content, None
    idx = content.index(_PLOTLY_HTML_MARKER)
    text = content[:idx].rstrip()
    marker = content[idx:]
    html_path = marker[len(_PLOTLY_HTML_MARKER):-len(" -->")].strip()
    return text, html_path


def _strip_markers(text: str) -> str:
    """Remove marker blocks from text for clean display."""
    result = text
    for start in (_IMAGE_MARKER, _FILE_MARKER, _PLOTLY_MARKER, _PLOTLY_HTML_MARKER, "<!-- ATTACHED_CONFIG:"):
        while start in result:
            idx = result.index(start)
            end = result.find(" -->", idx)
            if end == -1:
                break
            result = (result[:idx].rstrip() + result[end + 4 :].lstrip("\n"))
    return result


def render_message(content: str) -> None:
    """Render a chat message (markdown, images, Plotly charts, config JSON) to Streamlit."""
    st.markdown(_strip_markers(content))

    if _PLOTLY_MARKER in content:
        _, cache_path = split_plotly(content)
        if cache_path and os.path.isfile(cache_path):
            try:
                import plotly.io as pio
                fig = pio.from_json(Path(cache_path).read_text(encoding="utf-8"))
                st.plotly_chart(fig, use_container_width=True, key=f"plotly_{cache_path}")
            except Exception:
                pass

    if _PLOTLY_HTML_MARKER in content:
        _, html_path = split_plotly_html(content)
        if html_path and os.path.isfile(html_path):
            try:
                html_content = Path(html_path).read_text(encoding="utf-8", errors="ignore")
                st.components.v1.html(html_content, height=1200, scrolling=False)
            except Exception:
                pass

    if _IMAGE_MARKER in content:
        _, image_paths = split_images(content)
        for img_path in image_paths:
            if os.path.isfile(img_path):
                st.image(img_path, caption=os.path.basename(img_path))

    if _FILE_MARKER in content:
        _, file_paths = split_files(content)
        _render_files(file_paths)

    # Backward compat: old messages may have ATTACHED_CONFIG marker
    _OLD_CONFIG_MARKER = "<!-- ATTACHED_CONFIG:"
    if _OLD_CONFIG_MARKER in content:
        idx = content.index(_OLD_CONFIG_MARKER)
        end = content.find(" -->", idx)
        if end >= 0:
            paths_str = content[idx + len(_OLD_CONFIG_MARKER):end].strip()
            if paths_str:
                _render_files(paths_str.split("|"))


_EXT_LANG = {
    ".h": "cpp", ".hpp": "cpp", ".cpp": "cpp", ".cc": "cpp", ".c": "c",
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".sh": "bash", ".bash": "bash",
    ".json": "json", ".xml": "xml", ".yaml": "yaml", ".yml": "yaml",
    ".md": "markdown", ".html": "html", ".css": "css",
    ".log": "log", ".txt": "text", ".csv": "text",
    ".cmake": "cmake", ".makefile": "makefile",
}


def lang_for_file(name: str) -> str:
    """Return syntax highlight language for a filename."""
    lower = name.lower()
    for ext, lang in _EXT_LANG.items():
        if lower.endswith(ext):
            return lang
    if lower == "makefile" or lower == "cmakelists.txt":
        return "cmake"
    return "text"


def _is_config_file(path: str) -> bool:
    """True if path is a device config/SystemHealth JSON (often long, collapse by default)."""
    p = path.replace("\\", "/").lower()
    name = os.path.basename(path).lower()
    return (
        "/config/" in p or "/systemhealth/" in p
        or name in ("instrumentparameters.json", "systemhealthparameters.json", "networksettings.json")
    )


def _render_files(paths: list[str]) -> None:
    """Render arbitrary files (JSON or text) in expandable sections."""
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            raw = Path(path).read_text(encoding="utf-8", errors="replace")
            name = os.path.basename(path)
            expanded = not _is_config_file(path)
            with st.expander(f"📄 {name}", expanded=expanded):
                if path.lower().endswith(".json"):
                    try:
                        st.json(json.loads(raw))
                    except json.JSONDecodeError:
                        st.code(raw[:50000], language="json")
                else:
                    st.code(raw[:50000], language=lang_for_file(name))
        except (OSError, UnicodeDecodeError):
            pass


def relative_time(ts: float) -> str:
    diff = time.time() - ts
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff / 60)}m ago"
    if diff < 86400:
        return f"{int(diff / 3600)}h ago"
    return datetime.fromtimestamp(ts).strftime("%m/%d")
