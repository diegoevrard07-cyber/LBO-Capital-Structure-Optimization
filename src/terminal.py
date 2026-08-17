"""Shared presentation layer: the terminal CSS injected by every page.

Kept in one module so the main dashboard and the IC-memo page render with
identical typography, density, and chrome removal. Pure presentation — no
financial logic lives here.
"""

CSS = """
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap');

html, body, .stApp, [data-testid="stAppViewContainer"] {
    background-color: #0a0e14 !important;
    color: #d7dce3;
    font-family: 'JetBrains Mono', 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace !important;
    font-size: 12px;
}

/* kill Streamlit chrome */
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"],
.stAppDeployButton, [data-testid="stHeader"] { display: none !important; }

/* density */
.block-container { padding: 0.6rem 0.9rem 1rem !important; max-width: 100% !important; }
[data-testid="stVerticalBlock"] { gap: 0.35rem !important; }
[data-testid="stHorizontalBlock"] { gap: 0.5rem !important; }
[data-testid="stVerticalBlockBorderWrapper"] {
    background-color: #11161f !important; border: 1px solid #1f2733 !important;
    border-radius: 2px !important; box-shadow: none !important; padding: 0.45rem 0.6rem !important;
}
h1, h2, h3 { font-size: 12px !important; text-transform: uppercase; letter-spacing: 0.14em;
             color: #7d8590 !important; font-weight: 600 !important; margin: 0 !important; }
hr { border-color: #1f2733 !important; margin: 0.4rem 0 !important; }

/* sidebar */
[data-testid="stSidebar"] { background-color: #0d1219 !important; border-right: 1px solid #1f2733; }
[data-testid="stSidebar"] .block-container { padding: 0.6rem 0.7rem !important; }

/* widgets */
.stNumberInput input, .stTextInput input, [data-baseweb="select"] > div {
    background-color: #0a0e14 !important; border: 1px solid #1f2733 !important;
    border-radius: 2px !important; font-family: inherit !important; font-size: 12px !important;
}
.stButton button { border-radius: 2px !important; border: 1px solid #1f2733 !important;
    background-color: #11161f !important; font-family: inherit !important;
    text-transform: uppercase; letter-spacing: 0.1em; font-size: 11px !important; }
.stButton button[kind="primary"] { background-color: #ff9f1c !important; color: #0a0e14 !important;
    border-color: #ff9f1c !important; font-weight: 700 !important; }
[data-testid="stTabs"] button { text-transform: uppercase; letter-spacing: 0.1em; font-size: 11px !important; }
[data-testid="stDataFrame"] { font-family: inherit !important; }
.stAlert { border-radius: 2px !important; font-size: 12px !important; }
[data-testid="stExpander"] { border: 1px solid #1f2733 !important; border-radius: 2px !important; }

/* --- custom terminal components --- */
.statusbar { display: flex; justify-content: space-between; align-items: baseline;
    border-bottom: 1px solid #1f2733; padding: 2px 2px 6px; margin-bottom: 6px;
    font-size: 10px; letter-spacing: 0.16em; text-transform: uppercase; color: #7d8590; }
.statusbar .brand { color: #ff9f1c; font-weight: 700; }
.statusbar .live { color: #3fb950; }

/* metric strip: cells clip (never bleed into neighbours); the strip scrolls
   horizontally rather than overlapping on narrow windows */
.metric-strip { display: flex; border: 1px solid #1f2733; background: #11161f;
    border-radius: 2px; margin-bottom: 6px; overflow-x: auto; }
.metric-cell { flex: 1 0 auto; padding: 7px 12px 6px; border-left: 1px solid #1f2733;
    min-width: 0; overflow: hidden; }
.metric-cell:first-child { border-left: none; }
.metric-label { font-size: 9px; letter-spacing: 0.14em; text-transform: uppercase;
    color: #7d8590; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.metric-value { font-size: 18px; font-weight: 700; color: #d7dce3; line-height: 1.25;
    font-variant-numeric: tabular-nums; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; }
.metric-value.accent { color: #ff9f1c; }
.metric-value.pos { color: #3fb950; }
.metric-value.neg { color: #e5484d; }
.metric-sub { font-size: 9px; color: #7d8590; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; }

.panel-title { font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase;
    color: #7d8590; margin: 2px 0 0; }
.rec-line { font-size: 11px; letter-spacing: 0.04em; color: #d7dce3; padding: 2px 0 4px; }
.rec-line b { color: #ff9f1c; }
.rec-line .warn { color: #e5484d; }

table.term { width: 100%; border-collapse: collapse; font-size: 11px;
    font-variant-numeric: tabular-nums; table-layout: fixed; }
table.term th { text-align: right; font-size: 9px; letter-spacing: 0.12em; text-transform: uppercase;
    color: #7d8590; font-weight: 600; padding: 3px 8px; border-bottom: 1px solid #1f2733;
    overflow: hidden; text-overflow: ellipsis; }
table.term th:first-child, table.term td:first-child { text-align: left; }
table.term td { text-align: right; padding: 3px 8px; border-bottom: 1px solid #161c26;
    color: #d7dce3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
table.term tr:hover td { background: #161c26; }
table.term td.neg { color: #e5484d; }
table.term td.pos { color: #3fb950; }
table.term td.accent { color: #ff9f1c; }
table.term td.muted { color: #7d8590; }
table.term.compact { font-size: 10px; }
table.term.compact th, table.term.compact td { padding: 3px 5px; }
"""
