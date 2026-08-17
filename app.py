"""StackOptimal — terminal-styled Streamlit dashboard.

Run with:  python3 -m streamlit run app.py

Presentation layer only: every number rendered here comes from the engine
(src/) unchanged. The landing page is a composed "deal tearsheet" — status
bar, headline metric strip, the leverage x mix IRR heatmap as the main panel,
capital-structure stack, debt paydown, attribution waterfall, and a
credit-stats grid — with the efficient frontier, Monte Carlo, full waterfall
and IC memo in tabs below.

Visual language: near-black background, 1px panel borders, one amber accent
reserved for key numbers, red/green strictly for negative/positive values,
monospace tabular numerals throughout, Streamlit chrome hidden.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from src.debt import blended_rate
from src.inputs import CompanyData, MarketAssumptions, fetch_company
from src.memo import generate_memo
from src.optimizer import OptimizationResult, optimize
from src.risk import MCResult, frontier, monte_carlo

st.set_page_config(page_title="StackOptimal — LBO Structure Optimizer", layout="wide")

FRONTIER_MC_DRAWS = 1000  # per leverage level; keeps the frontier interactive (~1pp precision on distress)

# --- Palette ---------------------------------------------------------------
BG = "#0a0e14"  # near-black page background
PANEL = "#11161f"  # panel background
BORDER = "#1f2733"  # 1px panel borders / gridlines
ACCENT = "#ff9f1c"  # the one accent: key numbers and highlights
POS = "#3fb950"  # strictly positive values
NEG = "#e5484d"  # strictly negative values / breaches
TEXT = "#d7dce3"
MUTED = "#7d8590"  # labels, captions
STEEL = "#3d4b63"  # secondary series

CCY_SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}


# ---------------------------------------------------------------------------
# CSS — density, typography, chrome removal. Plain string (braces conflict
# with f-strings); injected once at import.
# ---------------------------------------------------------------------------

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

.metric-strip { display: flex; border: 1px solid #1f2733; background: #11161f;
    border-radius: 2px; margin-bottom: 6px; }
.metric-cell { flex: 1; padding: 7px 12px 6px; border-left: 1px solid #1f2733; min-width: 0; }
.metric-cell:first-child { border-left: none; }
.metric-label { font-size: 9px; letter-spacing: 0.14em; text-transform: uppercase;
    color: #7d8590; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.metric-value { font-size: 21px; font-weight: 700; color: #d7dce3; line-height: 1.25;
    font-variant-numeric: tabular-nums; white-space: nowrap; }
.metric-value.accent { color: #ff9f1c; }
.metric-value.pos { color: #3fb950; }
.metric-value.neg { color: #e5484d; }
.metric-sub { font-size: 9px; color: #7d8590; }

.panel-title { font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase;
    color: #7d8590; margin: 2px 0 0; }
.rec-line { font-size: 11px; letter-spacing: 0.04em; color: #d7dce3; padding: 2px 0 4px; }
.rec-line b { color: #ff9f1c; }
.rec-line .warn { color: #e5484d; }

table.term { width: 100%; border-collapse: collapse; font-size: 11px;
    font-variant-numeric: tabular-nums; }
table.term th { text-align: right; font-size: 9px; letter-spacing: 0.12em; text-transform: uppercase;
    color: #7d8590; font-weight: 600; padding: 3px 8px; border-bottom: 1px solid #1f2733; }
table.term th:first-child, table.term td:first-child { text-align: left; }
table.term td { text-align: right; padding: 3px 8px; border-bottom: 1px solid #161c26; color: #d7dce3; }
table.term tr:hover td { background: #161c26; }
table.term td.neg { color: #e5484d; }
table.term td.pos { color: #3fb950; }
table.term td.accent { color: #ff9f1c; }
table.term td.muted { color: #7d8590; }
"""

st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Formatting — one convention everywhere: x.xx× multiples, 0.0% rates,
# currency in millions with thousands separators. Never a raw float.
# ---------------------------------------------------------------------------


def fmult(v: float | None, digits: int = 2) -> str:
    return "n/a" if v is None else f"{v:.{digits}f}×"


def fpct(v: float | None, digits: int = 1) -> str:
    return "n/a" if v is None or not np.isfinite(v) else f"{v:.{digits}%}"


def fmm(v: float | None, ccy: str = "") -> str:
    if v is None:
        return "n/a"
    sym = CCY_SYMBOLS.get(ccy, f"{ccy} " if ccy else "")
    return f"{sym}{v:,.0f}mm"


def esc(s: object) -> str:
    return html.escape(str(s))


# ---------------------------------------------------------------------------
# HTML components.
# ---------------------------------------------------------------------------


def status_bar(deal: str, ccy: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    st.markdown(
        f'<div class="statusbar"><span><span class="brand">STACKOPTIMAL</span>'
        f" &nbsp;//&nbsp; LBO STRUCTURE OPTIMIZER</span>"
        f'<span>{esc(deal.upper())} &nbsp;·&nbsp; {esc(ccy)} &nbsp;·&nbsp; {ts}'
        f' &nbsp;·&nbsp; <span class="live">●</span></span></div>',
        unsafe_allow_html=True,
    )


def metric_strip(cells: list[tuple[str, str, str, str]]) -> None:
    """cells: (label, value, css_class, sublabel)."""
    parts = ['<div class="metric-strip">']
    for label, value, cls, sub in cells:
        sub_html = f'<div class="metric-sub">{esc(sub)}</div>' if sub else ""
        parts.append(
            f'<div class="metric-cell"><div class="metric-label">{esc(label)}</div>'
            f'<div class="metric-value {cls}">{esc(value)}</div>{sub_html}</div>'
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def terminal_table(headers: list[str], rows: list[list[tuple[str, str]]]) -> str:
    """rows: list of rows; each cell is (text, css_class). Returns HTML."""
    out = ['<table class="term"><thead><tr>']
    out += [f"<th>{esc(h)}</th>" for h in headers]
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>")
        for text, cls in row:
            out.append(f'<td class="{cls}">{esc(text)}</td>')
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


# ---------------------------------------------------------------------------
# Plotly — shared terminal layout.
# ---------------------------------------------------------------------------

MONO = dict(family="'JetBrains Mono','IBM Plex Mono',monospace", size=10, color=MUTED)


def terminal_layout(fig: go.Figure, height: int, title: str, **kwargs) -> go.Figure:
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=MONO,
        title=dict(text=title.upper(), font=dict(size=10, color=MUTED), x=0, xanchor="left"),
        margin=dict(l=48, r=16, t=30, b=34),
        legend=dict(font=dict(size=9), bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor=PANEL, bordercolor=BORDER, font=MONO),
        **kwargs,
    )
    axis = dict(gridcolor=BORDER, zerolinecolor=BORDER, linecolor=BORDER, tickfont=dict(size=9))
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    return fig


def heatmap_tearsheet(opt: OptimizationResult) -> go.Figure:
    """The optimizer's proof panel: base IRR over leverage x senior mix,
    annotated in-cell; infeasible cells dark with the reason on hover."""
    g = opt.grid
    mixes = sorted(g["mix"].unique())
    levs = sorted(g["leverage"].unique())
    z = np.full((len(mixes), len(levs)), np.nan)
    cell = np.empty((len(mixes), len(levs)), dtype=object)
    hover = np.empty((len(mixes), len(levs)), dtype=object)
    for _, r in g.iterrows():
        i, j = mixes.index(r["mix"]), levs.index(r["leverage"])
        if np.isfinite(r["base_irr"]):
            z[i, j] = r["base_irr"]
            cell[i, j] = f"{r['base_irr'] * 100:.1f}"
            status = "ADMISSIBLE" if r["admissible"] else f"FAILS STRESS — {r['failure']}"
        else:
            cell[i, j] = ""
            status = r["failure"] or "infeasible"
        hover[i, j] = f"{r['leverage']:.2f}× @ {r['mix']:.0%} senior — {status}"

    amber_on_dark = [[0.0, "#141a24"], [0.4, "#3a2c10"], [0.75, "#8a5a10"], [1.0, ACCENT]]
    fig = go.Figure(
        go.Heatmap(
            z=z, x=[f"{lv:.2f}" for lv in levs], y=[f"{mx:.0%}" for mx in mixes],
            text=cell, texttemplate="%{text}", textfont=dict(size=8, color="#e8edf4"),
            hovertext=hover, hoverinfo="text",
            colorscale=amber_on_dark, showscale=True,
            colorbar=dict(title=dict(text="IRR %", font=dict(size=9)), tickfont=dict(size=9),
                          thickness=8, outlinewidth=0),
        )
    )
    terminal_layout(fig, 430, "IRR sensitivity — total leverage × senior mix (base case, %)",)
    fig.update_layout(plot_bgcolor="#161c26")  # infeasible (NaN) cells read as dead panel
    fig.update_xaxes(title=dict(text="TOTAL LEVERAGE (× EBITDA)", font=dict(size=9)), tickfont=dict(size=8))
    fig.update_yaxes(title=dict(text="SENIOR MIX", font=dict(size=9)), tickfont=dict(size=8))
    return fig


def cap_stack_chart(stack, equity0: float, ccy: str) -> go.Figure:
    """The capitalization as one horizontal stacked bar, seniority left to right."""
    colors = {"Senior TLB": ACCENT, "Second Lien": "#b26a00", "Mezzanine": "#6b4a1f"}
    fig = go.Figure()
    for t in stack:
        name = t.tranche.name
        fig.add_trace(
            go.Bar(
                y=["CAP STACK"], x=[t.amount], name=name, orientation="h",
                marker=dict(color=colors.get(name, STEEL)),
                text=f"{name.upper()} {t.turns:.2f}×", textposition="inside",
                textfont=dict(size=9, color="#0a0e14"),
                hovertemplate=f"{name}: {fmm(t.amount, ccy)} ({t.turns:.2f}×)<extra></extra>",
            )
        )
    fig.add_trace(
        go.Bar(
            y=["CAP STACK"], x=[equity0], name="Sponsor equity", orientation="h",
            marker=dict(color=STEEL), text="EQUITY", textposition="inside",
            textfont=dict(size=9, color=TEXT),
            hovertemplate=f"Equity: {fmm(equity0, ccy)}<extra></extra>",
        )
    )
    terminal_layout(fig, 150, "Capitalization at close")
    fig.update_layout(barmode="stack", showlegend=False, bargap=0.15)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def paydown_chart(yearly: pd.DataFrame, stack) -> go.Figure:
    """Debt balances by tranche over the hold — the deleveraging story."""
    years = [0] + [int(y) for y in yearly["year"]]
    colors = {"Senior TLB": ACCENT, "Second Lien": "#b26a00", "Mezzanine": "#6b4a1f"}
    fig = go.Figure()
    for t in stack:
        name = t.tranche.name
        fig.add_trace(
            go.Scatter(x=years, y=[t.amount] + yearly[f"balance_{name}"].tolist(), name=name,
                       stackgroup="debt", mode="lines", line=dict(color=colors.get(name, STEEL), width=1.5),
                       hovertemplate=f"{name}: %{{y:,.0f}}mm<extra></extra>")
        )
    fig.add_trace(
        go.Scatter(x=years, y=[0.0] + yearly["cash_closing"].tolist(), name="Cash",
                   mode="lines", line=dict(color=MUTED, width=1, dash="dot"),
                   hovertemplate="Cash: %{y:,.0f}mm<extra></extra>")
    )
    terminal_layout(fig, 260, "Debt paydown — balances by tranche (mm)")
    fig.update_xaxes(title=dict(text="YEAR", font=dict(size=9)), dtick=1)
    return fig


def attribution_waterfall(attribution: dict) -> go.Figure:
    """Value-creation bridge as a true waterfall: growth / multiple / paydown / fees -> total."""
    fig = go.Figure(
        go.Waterfall(
            x=["EBITDA GROWTH", "MULTIPLE EXP.", "DEBT PAYDOWN", "FEES", "TOTAL PROFIT"],
            y=[attribution["ebitda_growth"], attribution["multiple_expansion"],
               attribution["debt_paydown"], attribution["fees"], attribution["total_profit"]],
            measure=["relative", "relative", "relative", "relative", "total"],
            increasing=dict(marker=dict(color=POS)),
            decreasing=dict(marker=dict(color=NEG)),
            totals=dict(marker=dict(color=ACCENT)),
            connector=dict(line=dict(color=BORDER, width=1)),
            texttemplate="%{y:,.0f}", textfont=dict(size=9), textposition="outside",
            hovertemplate="%{x}: %{y:,.0f}mm<extra></extra>",
        )
    )
    terminal_layout(fig, 260, "Returns attribution — equity profit bridge (mm)")
    return fig


def frontier_chart(f: pd.DataFrame, opt_mc: MCResult, naive_mc: MCResult | None, survivable_x: float) -> go.Figure:
    """Median IRR vs P(distress) across leverage levels."""
    fig = go.Figure()
    fig.add_vrect(x0=0, x1=survivable_x, fillcolor=ACCENT, opacity=0.07, line_width=0,
                  annotation_text="SURVIVABLE ZONE", annotation_position="top left",
                  annotation_font=dict(size=9, color=MUTED))
    fig.add_trace(
        go.Scatter(
            x=f["p_distress"], y=f["median_irr"], mode="lines+markers",
            customdata=np.stack([f["leverage"], f["p5"], f["p95"]], axis=-1),
            hovertemplate=("Leverage %{customdata[0]:.1f}×<br>Median IRR %{y:.1%}<br>"
                           "P(distress) %{x:.1%}<br>P5/P95 %{customdata[1]:.1%} / %{customdata[2]:.1%}<extra></extra>"),
            name="Leverage path", line=dict(color=ACCENT, width=2), marker=dict(size=6),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[opt_mc.p_distress], y=[opt_mc.median], mode="markers", name="Optimum (survivable)",
            marker=dict(symbol="star", size=16, color=ACCENT, line=dict(color=BG, width=1)),
            hovertemplate=f"OPTIMUM {opt_mc.leverage:.2f}×<br>Median IRR {opt_mc.median:.1%}<br>"
                          f"P(distress) {opt_mc.p_distress:.1%}<extra></extra>",
        )
    )
    if naive_mc is not None:
        fig.add_trace(
            go.Scatter(
                x=[naive_mc.p_distress], y=[naive_mc.median], mode="markers", name="Naive max-IRR",
                marker=dict(symbol="diamond", size=11, color=NEG),
                hovertemplate=f"NAIVE {naive_mc.leverage:.2f}×<br>Median IRR {naive_mc.median:.1%}<br>"
                              f"P(distress) {naive_mc.p_distress:.1%}<extra></extra>",
            )
        )
    terminal_layout(fig, 460, "Efficient frontier — median IRR vs P(distress)")
    fig.update_layout(xaxis_tickformat=".0%", yaxis_tickformat=".0%",
                      xaxis_title=dict(text="P(DISTRESS)", font=dict(size=9)),
                      yaxis_title=dict(text="MEDIAN IRR", font=dict(size=9)),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return fig


def covenant_chart(yearly: pd.DataFrame) -> go.Figure:
    fig = make_subplots(cols=2, subplot_titles=("INTEREST COVERAGE VS MINIMUM", "NET LEVERAGE VS COVENANT"))
    fig.add_trace(go.Scatter(x=yearly["year"], y=yearly["coverage_ratio"], name="Coverage",
                             mode="lines+markers", line=dict(color=ACCENT, width=1.5),
                             marker=dict(size=5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=yearly["year"], y=yearly["coverage_covenant"], name="Minimum",
                             mode="lines", line=dict(color=NEG, dash="dash", width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=yearly["year"], y=yearly["leverage_ratio"], name="Net leverage",
                             mode="lines+markers", line=dict(color=ACCENT, width=1.5),
                             marker=dict(size=5)), row=1, col=2)
    fig.add_trace(go.Scatter(x=yearly["year"], y=yearly["leverage_covenant"], name="Covenant",
                             mode="lines", line=dict(color=NEG, dash="dash", width=1)), row=1, col=2)
    terminal_layout(fig, 300, "Covenant headroom by year")
    fig.update_layout(showlegend=False)
    fig.update_annotations(font=dict(size=9, color=MUTED))
    return fig


def mc_histogram(mc: MCResult) -> go.Figure:
    fig = go.Figure(go.Histogram(x=mc.irrs, nbinsx=60, marker=dict(color=STEEL, line=dict(color=BORDER, width=0.5))))
    for value, label, color in [(mc.p5, "P5", NEG), (mc.median, "MEDIAN", ACCENT), (mc.p95, "P95", POS)]:
        if np.isfinite(value):
            fig.add_vline(x=value, line=dict(color=color, dash="dash", width=1),
                          annotation_text=f"{label} {value:.0%}", annotation_position="top",
                          annotation_font=dict(size=9, color=color))
    terminal_layout(fig, 380, f"IRR distribution — {mc.n:,} draws (surviving; wiped equity at −100%)")
    fig.update_layout(xaxis_tickformat=".0%", xaxis_title=dict(text="IRR", font=dict(size=9)),
                      yaxis_title=dict(text="FREQ", font=dict(size=9)))
    return fig


# ---------------------------------------------------------------------------
# Tearsheet tables (from existing computed outputs only).
# ---------------------------------------------------------------------------


def su_table_html(entry: dict, stack, ccy: str) -> str:
    uses = [("PURCHASE PRICE (EV)", entry["ev"]), ("FEES", entry["fees"]),
            ("TOTAL USES", entry["ev"] + entry["fees"])]
    sources = [(f"{t.tranche.name.upper()} {t.turns:.2f}×", t.amount) for t in stack]
    sources += [("SPONSOR EQUITY", entry["equity"]), ("TOTAL SOURCES", entry["total_debt"] + entry["equity"])]
    rows = []
    for i in range(max(len(uses), len(sources))):
        u = uses[i] if i < len(uses) else ("", None)
        s = sources[i] if i < len(sources) else ("", None)
        u_cls = "accent" if u[0].startswith("TOTAL") else ""
        s_cls = "accent" if s[0].startswith("TOTAL") else ""
        rows.append([
            (u[0], "muted" if not u_cls else u_cls), (fmm(u[1], ccy) if u[1] is not None else "", u_cls),
            (s[0], "muted" if not s_cls else s_cls), (fmm(s[1], ccy) if s[1] is not None else "", s_cls),
        ])
    return terminal_table(["USES", "", "SOURCES", ""], rows)


def credit_stats_html(yearly: pd.DataFrame, a: MarketAssumptions, ccy: str) -> str:
    """Per-year credit grid; covenant breaches rendered red."""
    rows = []
    for _, r in yearly.iterrows():
        cov_breach = r["coverage_ratio"] < a.min_interest_coverage
        lev_breach = r["leverage_ratio"] > r["leverage_covenant"]
        rows.append([
            (f"Y{int(r['year'])}", "muted"),
            (f"{r['ebitda']:,.0f}", ""),
            (f"{r['cfads']:,.0f}", ""),
            (f"{r['interest_total']:,.0f}", ""),
            (fmult(r["coverage_ratio"]), "neg" if cov_breach else "pos"),
            (fmult(r["leverage_ratio"]), "neg" if lev_breach else ""),
            (fmult(r["leverage_covenant"]), "muted"),
            (f"{r['cash_closing']:,.0f}", ""),
        ])
    return terminal_table(
        ["YR", "EBITDA", "CFADS", "CASH INT", "COV", "ND/EBITDA", "COV CAP", "CASH"], rows
    )


# ---------------------------------------------------------------------------
# Data access (cached) and assumption assembly from sidebar widgets.
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False, ttl=3600)
def cached_fetch(ticker: str) -> CompanyData:
    """Fetch fundamentals; cached for an hour so re-runs don't hammer Yahoo."""
    return fetch_company(ticker)


def sidebar_assumptions() -> MarketAssumptions:
    """Every key assumption as an editable widget, pre-populated with defaults."""
    d = MarketAssumptions()
    st.sidebar.header("Assumptions")

    with st.sidebar.expander("Rates & tranches", expanded=False):
        base_rate = st.number_input("Base rate (SONIA proxy)", 0.0, 0.15, d.base_rate, 0.0025, format="%.4f")
        senior_margin = st.number_input("Senior TLB margin", 0.0, 0.10, d.senior_margin, 0.0025, format="%.4f")
        second_lien_margin = st.number_input("Second-lien margin", 0.0, 0.15, d.second_lien_margin, 0.0025, format="%.4f")
        mezz_rate = st.number_input("Mezzanine fixed coupon", 0.0, 0.25, d.mezz_rate, 0.005, format="%.3f")
        senior_cap = st.number_input("Senior cap (turns)", 0.0, 8.0, d.senior_cap, 0.25)
        second_lien_cap = st.number_input("Second-lien cap (turns)", 0.0, 4.0, d.second_lien_cap, 0.25)
        mezz_cap = st.number_input("Mezzanine cap (turns)", 0.0, 4.0, d.mezz_cap, 0.25)

    with st.sidebar.expander("Covenants", expanded=False):
        min_cov = st.number_input("Min interest coverage", 1.0, 5.0, d.min_interest_coverage, 0.25)
        headroom = st.number_input("Leverage covenant headroom (turns)", 0.0, 2.0, d.leverage_covenant_headroom, 0.25)
        stepdown = st.number_input("Leverage step-down per year", 0.0, 1.0, d.leverage_stepdown, 0.25)
        floor = st.number_input("Leverage covenant floor (turns)", 0.0, 8.0, d.leverage_floor, 0.25)

    with st.sidebar.expander("Deal structure", expanded=False):
        hold = st.number_input("Hold period (years)", 3, 8, d.hold_years, 1)
        entry_premium = st.number_input("Entry premium over market (turns)", 0.0, 4.0, d.entry_premium_turns, 0.25)
        exit_premium = st.number_input("Exit multiple vs entry (turns)", -3.0, 3.0, d.exit_multiple_premium, 0.25)
        sweep = st.slider("Cash sweep (% of post-amort FCF)", 0, 100, int(d.cash_sweep_pct * 100)) / 100
        debt_fee = st.number_input("Debt fees (% of debt)", 0.0, 0.06, d.debt_fee_pct, 0.005, format="%.3f")
        ev_fee = st.number_input("Transaction fees (% of EV)", 0.0, 0.05, d.ev_fee_pct, 0.005, format="%.3f")
        wc = st.number_input("Working capital (% of incremental revenue)", 0.0, 0.10, d.wc_pct_of_delta_rev, 0.01, format="%.2f")

    with st.sidebar.expander("Stress & Monte Carlo", expanded=False):
        g_haircut = st.slider("Stress growth haircut (%)", 0, 60, int(d.stress_growth_haircut * 100)) / 100
        e_shock = st.slider("Stress year-1 EBITDA shock (%)", 0, 30, int(d.stress_ebitda_shock * 100)) / 100
        x_shock = st.number_input("Stress exit-multiple shock (turns)", 0.0, 4.0, d.stress_exit_multiple_shock, 0.25)
        draws = st.number_input("Monte Carlo draws", 1000, 20000, d.mc_draws, 1000)

    return MarketAssumptions(
        base_rate=base_rate, senior_margin=senior_margin, second_lien_margin=second_lien_margin,
        mezz_rate=mezz_rate, senior_cap=senior_cap, second_lien_cap=second_lien_cap, mezz_cap=mezz_cap,
        min_interest_coverage=min_cov, leverage_covenant_headroom=headroom,
        leverage_stepdown=stepdown, leverage_floor=floor, hold_years=int(hold),
        entry_premium_turns=entry_premium, exit_multiple_premium=exit_premium,
        cash_sweep_pct=sweep, debt_fee_pct=debt_fee, ev_fee_pct=ev_fee, wc_pct_of_delta_rev=wc,
        stress_growth_haircut=g_haircut, stress_ebitda_shock=e_shock, stress_exit_multiple_shock=x_shock,
        mc_draws=int(draws),
    )


# ---------------------------------------------------------------------------
# Page assembly.
# ---------------------------------------------------------------------------


def main() -> None:
    st.sidebar.header("Target")
    ticker = st.sidebar.text_input("Ticker (e.g. TSCO.L, MKS.L, SDF.DE)", value="TSCO.L")
    fetch_clicked = st.sidebar.button("Fetch company", type="secondary")
    assumptions = sidebar_assumptions()
    run_clicked = st.sidebar.button("Run optimization", type="primary")

    if fetch_clicked or "company" not in st.session_state:
        try:
            with st.spinner(f"Fetching {ticker}…"):
                st.session_state.company = cached_fetch(ticker.strip())
            st.session_state.pop("results", None)  # stale results for a different target
        except ValueError as exc:
            st.session_state.pop("company", None)
            status_bar("NO TARGET", "—")
            st.error(str(exc))
            return

    company: CompanyData = st.session_state.company
    status_bar(company.name, company.currency)

    if company.warnings:
        st.warning("DATA QUALITY — every fallback is disclosed:  " + "  |  ".join(company.warnings))

    m_entry = company.ev_ebitda + assumptions.entry_premium_turns
    metric_strip([
        ("TARGET", company.name[:18].upper(), "", company.ticker),
        ("ENTRY EBITDA", fmm(company.ebitda, company.currency), "", f"REV {fmm(company.revenue, company.currency)}"),
        ("MKT EV/EBITDA", fmult(company.ev_ebitda, 1), "", f"+{assumptions.entry_premium_turns:.1f}× premium"),
        ("ENTRY MULTIPLE", fmult(m_entry, 1), "", "at close"),
        ("IMPLIED EV", fmm(m_entry * company.ebitda, company.currency), "", "enterprise value"),
    ])

    if run_clicked:
        try:
            with st.status("Optimizing capital structure…", expanded=True) as status:
                st.write("Scanning the leverage × mix grid (base + stress at every point)…")
                opt = optimize(company, assumptions)
                st.write(f"Evaluated {len(opt.grid)} structures.")
                opt_mc = naive_mc = front = memo = None
                if opt.optimum is not None:
                    st.write(f"Running {assumptions.mc_draws:,}-draw Monte Carlo at the optimum…")
                    opt_mc = monte_carlo(company, assumptions, opt.optimum.stack)
                    if opt.naive is not None and opt.naive.stack is not None:
                        naive_mc = monte_carlo(company, assumptions, opt.naive.stack, n=FRONTIER_MC_DRAWS)
                    st.write("Tracing the efficient frontier…")
                    front = frontier(company, assumptions, mix=opt.optimum.mix, n=FRONTIER_MC_DRAWS)
                    memo = generate_memo(company, assumptions, opt, opt_mc)
                else:
                    memo = generate_memo(company, assumptions, opt, None)
                st.session_state.results = dict(opt=opt, opt_mc=opt_mc, naive_mc=naive_mc,
                                                frontier=front, memo=memo)
                status.update(label="Done.", state="complete")
        except Exception as exc:  # never show a raw stack trace to an IC
            st.error(f"The analysis could not be completed: {exc}")
            return

    if "results" not in st.session_state:
        st.markdown(
            '<div class="rec-line">AWAITING RUN — fetch a target, review assumptions, press '
            "<b>RUN OPTIMIZATION</b>.</div>",
            unsafe_allow_html=True,
        )
        return

    res = st.session_state.results
    opt: OptimizationResult = res["opt"]

    if opt.optimum is None:
        st.error("NO ADMISSIBLE CAPITAL STRUCTURE — no leverage × mix combination is feasible in the "
                 "base case AND survives the stressed downside. The memo names the binding constraint.")
        with st.tabs(["IC MEMO"])[0]:
            st.markdown(res["memo"])
            st.download_button("Download memo (markdown)", res["memo"], "ic_memo.md")
        return

    o = opt.optimum
    base = o.base
    senior_turns = sum(t.turns for t in o.stack if t.tranche.seniority == 1)
    junior_turns = o.leverage - senior_turns

    # ---------------- Tearsheet ----------------
    rec = (
        f'<div class="rec-line">OPTIMAL STRUCTURE — <b>{o.leverage:.2f}×</b> TOTAL LEVERAGE · '
        f"{senior_turns:.1f}/{junior_turns:.1f} SR/JR · IRR <b>{fpct(base.irr)}</b> · MOIC "
        f"<b>{fmult(base.moic)}</b> · SURVIVES STRESS "
        f"(−{assumptions.stress_growth_haircut:.0%} GROWTH, −{assumptions.stress_exit_multiple_shock:.1f}× EXIT)"
    )
    if opt.naive is not None and opt.naive_gap_irr is not None and opt.naive_gap_irr > 1e-9:
        rec += (
            f' &nbsp;&nbsp;<span class="warn">▲ NAIVE {opt.naive.leverage:.2f}× @ '
            f"{fpct(opt.naive.base.irr)} FAILS STRESS — SURVIVABILITY COST {fpct(opt.naive_gap_irr)}</span>"
        )
    rec += "</div>"
    st.markdown(rec, unsafe_allow_html=True)
    if opt.on_boundary:
        st.info(f"GRID-BOUNDARY DISCLOSURE — {opt.boundary_note}")

    min_cov = base.yearly["coverage_ratio"].min()
    metric_strip([
        ("IRR (BASE)", fpct(base.irr), "accent", f"P5/P95 {fpct(res['opt_mc'].p5)}/{fpct(res['opt_mc'].p95)}"),
        ("MOIC", fmult(base.moic), "accent", f"profit {fmm(base.attribution['total_profit'], company.currency)}"),
        ("LEVERAGE @ CLOSE", fmult(o.leverage), "accent", f"{o.mix:.0%} senior mix"),
        ("ENTRY → EXIT", f"{fmult(base.entry['entry_multiple'], 1)} → {fmult(base.exit['exit_multiple'], 1)}", "", "no expansion assumed"),
        ("MIN COVERAGE", fmult(min_cov), "pos" if min_cov >= assumptions.min_interest_coverage else "neg",
         f"covenant {assumptions.min_interest_coverage:.1f}×"),
        ("EQUITY CHECK", fmm(base.entry["equity"], company.currency), "", f"of {fmm(base.entry['ev'] + base.entry['fees'], company.currency)} uses"),
    ])

    main_col, side_col = st.columns([2.1, 1.0])
    with main_col:
        with st.container(border=True):
            st.plotly_chart(heatmap_tearsheet(opt), use_container_width=True)
    with side_col:
        with st.container(border=True):
            st.plotly_chart(cap_stack_chart(o.stack, base.entry["equity"], company.currency),
                            use_container_width=True)
            st.markdown('<div class="panel-title">Sources &amp; Uses</div>', unsafe_allow_html=True)
            st.markdown(su_table_html(base.entry, o.stack, company.currency), unsafe_allow_html=True)

    low_left, low_right = st.columns(2)
    with low_left:
        with st.container(border=True):
            st.plotly_chart(paydown_chart(base.yearly, o.stack), use_container_width=True)
    with low_right:
        with st.container(border=True):
            st.plotly_chart(attribution_waterfall(base.attribution), use_container_width=True)

    with st.container(border=True):
        st.markdown('<div class="panel-title">Credit statistics by year — base case</div>',
                    unsafe_allow_html=True)
        st.markdown(credit_stats_html(base.yearly, assumptions, company.currency), unsafe_allow_html=True)

    # ---------------- Tabs ----------------
    tab_frontier, tab_mc, tab_detail, tab_memo = st.tabs(
        ["EFFICIENT FRONTIER", "MONTE CARLO", "WATERFALL DETAIL", "IC MEMO"]
    )

    with tab_frontier:
        st.plotly_chart(frontier_chart(res["frontier"], res["opt_mc"], res["naive_mc"],
                                       _survivable_boundary(opt, res)),
                        use_container_width=True)
        st.caption(f"Frontier: {FRONTIER_MC_DRAWS:,} MC draws per leverage level at the optimum's senior mix; "
                   f"optimum uses {assumptions.mc_draws:,} draws. Fixed seed — curves comparable across levels.")

    with tab_mc:
        mc: MCResult = res["opt_mc"]
        metric_strip([
            ("P(COVENANT BREACH)", fpct(mc.p_breach), "neg" if mc.p_breach > 0 else "", ""),
            ("P(EQUITY WIPEOUT)", fpct(mc.p_wipeout), "neg" if mc.p_wipeout > 0 else "", ""),
            ("P(DISTRESS)", fpct(mc.p_distress), "neg" if mc.p_distress > 0 else "", ""),
            ("P(IRR<0 | SURVIVE)", fpct(mc.p_negative_irr), "neg" if mc.p_negative_irr else "", ""),
        ])
        st.plotly_chart(mc_histogram(mc), use_container_width=True)
        stress = o.stress
        comp_rows = [
            [("IRR", "muted"), (fpct(base.irr), "accent"),
             (fpct(stress.irr) if stress.feasible and stress.irr is not None else "n/a", "")],
            [("MOIC", "muted"), (fmult(base.moic), "accent"),
             (fmult(stress.moic) if stress.feasible and stress.moic is not None
              else ("0.00× (wiped)" if stress.equity_wiped else "n/a"), "")],
            [("COVENANT-COMPLIANT", "muted"), ("YES", "pos"),
             ("YES" if stress.feasible else f"NO — {stress.failure_reason}",
              "pos" if stress.feasible else "neg")],
            [("MIN COVERAGE", "muted"), (fmult(base.yearly["coverage_ratio"].min()), ""),
             (fmult(stress.yearly["coverage_ratio"].min()) if not stress.yearly.empty else "n/a", "")],
            [("MAX NET LEVERAGE", "muted"), (fmult(base.yearly["leverage_ratio"].max()), ""),
             (fmult(stress.yearly["leverage_ratio"].max()) if not stress.yearly.empty else "n/a", "")],
        ]
        st.markdown('<div class="panel-title">Base vs stressed downside</div>', unsafe_allow_html=True)
        st.markdown(terminal_table(["", "BASE", "STRESS"], comp_rows), unsafe_allow_html=True)

    with tab_detail:
        st.markdown(
            f'<div class="rec-line">BLENDED COST OF DEBT <b>{fpct(blended_rate(o.stack, assumptions.base_rate), 2)}</b>'
            f" @ {fpct(assumptions.base_rate, 2)} BASE</div>",
            unsafe_allow_html=True,
        )
        st.plotly_chart(covenant_chart(base.yearly), use_container_width=True)
        st.markdown('<div class="panel-title">Full yearly waterfall</div>', unsafe_allow_html=True)
        show = base.yearly.copy()
        money_cols = [c for c in show.columns if c not in ("year", "covenant_breach")
                      and not c.startswith(("coverage", "leverage"))]
        show[money_cols] = show[money_cols].round(1)
        st.dataframe(show, use_container_width=True, hide_index=True)

    with tab_memo:
        st.markdown(res["memo"])
        st.download_button("Download memo (markdown)", res["memo"], "ic_memo.md")


def _survivable_boundary(opt: OptimizationResult, res: dict) -> float:
    """X-coordinate (P-distress) of the edge of the survivable zone: the highest
    leverage at the frontier's mix that still passes the deterministic stress."""
    o = opt.optimum
    g = opt.grid
    admissible = g[(g["mix"] == o.mix) & g["admissible"]]
    if admissible.empty:
        return 0.0
    max_lev = admissible["leverage"].max()
    f = res["frontier"]
    row = f[np.isclose(f["leverage"], max_lev)]
    if row.empty:  # frontier step may skip the exact grid level; interpolate conservatively
        below = f[f["leverage"] <= max_lev]
        return float(below["p_distress"].max()) if not below.empty else 0.0
    return float(row["p_distress"].iloc[0])


if __name__ == "__main__":
    main()
