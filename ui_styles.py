"""Streamlit UI styles (CSS)."""
SIDEBAR_AND_MAIN_CSS = """
<style>
/* ---- sidebar ---- */
section[data-testid="stSidebar"] {
    background-color: #171720;
    min-width: 260px;
}
section[data-testid="stSidebar"] .stButton > button {
    width: 100%;
    text-align: left;
    padding: 0.45rem 0.7rem;
    border-radius: 0.5rem;
    border: none;
    background: transparent;
    color: #c9d1d9;
    font-size: 0.84rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    background-color: #2a2a3d;
}
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] li,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] .stMarkdown,
section[data-testid="stSidebar"] td,
section[data-testid="stSidebar"] th,
section[data-testid="stSidebar"] h4,
section[data-testid="stSidebar"] blockquote {
    color: #e0e0e0 !important;
}
section[data-testid="stSidebar"] code {
    color: #79c0ff !important;
    background-color: rgba(110,118,129,0.2) !important;
}
section[data-testid="stSidebar"] blockquote {
    border-left-color: #3a3a5c !important;
}
section[data-testid="stSidebar"] hr {
    border-color: #2a2a3d !important;
}
/* ---- main area ---- */
.main .block-container {
    max-width: 840px;
    padding-top: 1.2rem;
}
/* hide chrome */
#MainMenu, footer, header {visibility: hidden;}
/* tool indicator */
.tool-ind {
    font-size: 0.78rem;
    color: #777;
    padding: 1px 0;
    font-family: monospace;
}
/* welcome greeting */
.welcome-card {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    border-radius: 12px;
    padding: 1.75rem 2rem;
    margin-bottom: 1.5rem;
    border: 1px solid rgba(255,255,255,0.06);
    box-shadow: 0 4px 20px rgba(0,0,0,0.15);
}
.welcome-card .greeting {
    font-size: 1.35rem;
    font-weight: 600;
    color: #e6edf3;
    margin: 0 0 0.25rem 0;
    letter-spacing: -0.02em;
}
.welcome-card .greeting .ip {
    color: #58a6ff;
    font-family: ui-monospace, monospace;
    font-weight: 500;
}
.welcome-card .sub {
    color: #8b949e;
    font-size: 0.9rem;
    margin: 0;
    line-height: 1.5;
}
</style>
"""
