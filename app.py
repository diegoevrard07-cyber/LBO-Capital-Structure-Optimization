"""StackOptimal — Streamlit dashboard.

Run with:  streamlit run app.py

Flow: fetch a ticker -> review/adjust assumptions -> run the optimizer ->
inspect the efficient frontier (centerpiece), the leverage x mix heatmap, the
optimal structure's waterfall and covenant headroom, the Monte Carlo risk
profile, and the auto-generated IC memo.

Every failure mode — bad ticker, missing data, no admissible structure — is
surfaced as an informative message, never a raw stack trace.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from src.debt import blended_rate, build_stack
from src.inputs import CompanyData, MarketAssumptions, fetch_company
from src.memo import generate_memo
from src.model import base_case, run_lbo, run_stress
from src.optimizer import OptimizationResult, optimize
from src.risk import MCResult, frontier, monte_carlo

st.set_page_config(page_title="StackOptimal — LBO Capital-Structure Optimizer", layout="wide")

FRONTIER_MC_DRAWS = 1000  # per leverage level; keeps the frontier interactive (~1pp precision on distress)


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
# Chart builders (Plotly).
# ---------------------------------------------------------------------------


def frontier_chart(f: pd.DataFrame, opt_mc: MCResult, naive_mc: MCResult | None, survivable_x: float) -> go.Figure:
    """The centerpiece: median IRR vs P(distress) across leverage levels."""
    fig = go.Figure()

    # Survivable zone: leverage levels that pass the deterministic stress test.
    fig.add_vrect(
        x0=0, x1=survivable_x, fillcolor="green", opacity=0.08, line_width=0,
        annotation_text="survivable zone", annotation_position="top left",
    )

    fig.add_trace(
        go.Scatter(
            x=f["p_distress"], y=f["median_irr"], mode="lines+markers",
            customdata=np.stack([f["leverage"], f["p5"], f["p95"]], axis=-1),
            hovertemplate=(
                "Leverage %{customdata[0]:.1f}x<br>Median IRR %{y:.1%}<br>"
                "P(distress) %{x:.1%}<br>P5/P95 %{customdata[1]:.1%} / %{customdata[2]:.1%}<extra></extra>"
            ),
            name="Leverage path", line=dict(color="#1f4e79", width=3), marker=dict(size=9),
        )
    )

    fig.add_trace(
        go.Scatter(
            x=[opt_mc.p_distress], y=[opt_mc.median], mode="markers", name="Optimum (survivable)",
            marker=dict(symbol="star", size=22, color="#f2a900", line=dict(color="black", width=1)),
            hovertemplate=f"OPTIMUM {opt_mc.leverage:.2f}x<br>Median IRR {opt_mc.median:.1%}<br>P(distress) {opt_mc.p_distress:.1%}<extra></extra>",
        )
    )
    if naive_mc is not None:
        fig.add_trace(
            go.Scatter(
                x=[naive_mc.p_distress], y=[naive_mc.median], mode="markers", name="Naive max-IRR",
                marker=dict(symbol="diamond", size=14, color="#c00000"),
                hovertemplate=f"NAIVE {naive_mc.leverage:.2f}x<br>Median IRR {naive_mc.median:.1%}<br>P(distress) {naive_mc.p_distress:.1%}<extra></extra>",
            )
        )

    fig.update_layout(
        template="plotly_white", height=520,
        title=dict(text="The LBO efficient frontier — return vs the probability of losing the company", font=dict(size=18)),
        xaxis_title="P(distress) — covenant breach or equity wipeout",
        yaxis_title="Median IRR (Monte Carlo)",
        xaxis_tickformat=".0%", yaxis_tickformat=".0%",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(t=80),
    )
    return fig


def heatmap_chart(opt: OptimizationResult) -> go.Figure:
    """Base IRR over the leverage x mix grid; infeasible cells greyed with reasons."""
    g = opt.grid
    mixes = sorted(g["mix"].unique())
    levs = sorted(g["leverage"].unique())
    z = np.full((len(mixes), len(levs)), np.nan)
    text = np.empty((len(mixes), len(levs)), dtype=object)
    for _, r in g.iterrows():
        i, j = mixes.index(r["mix"]), levs.index(r["leverage"])
        if np.isfinite(r["base_irr"]):
            z[i, j] = r["base_irr"]
            status = "admissible" if r["admissible"] else f"fails stress: {r['failure']}"
        else:
            status = r["failure"] or "infeasible"
        text[i, j] = f"{r['leverage']:.2f}x @ {r['mix']:.0%} senior<br>{status}"

    fig = go.Figure(
        go.Heatmap(
            z=z, x=[f"{lv:.2f}x" for lv in levs], y=[f"{mx:.0%}" for mx in mixes],
            hovertext=text, hoverinfo="text", colorscale="Viridis", colorbar=dict(title="IRR", tickformat=".0%"),
        )
    )
    fig.update_layout(
        template="plotly_white", height=480, plot_bgcolor="#d9d9d9",  # grey shows through NaN cells
        title="Base-case IRR across the leverage x senior-mix grid (grey = infeasible — hover for the reason)",
        xaxis_title="Total leverage (turns of EBITDA)", yaxis_title="Senior mix",
    )
    return fig


def balances_chart(yearly: pd.DataFrame, stack) -> go.Figure:
    """Stacked area of tranche balances + cash over the hold (year 0 = close)."""
    years = [0] + [int(y) for y in yearly["year"]]
    fig = go.Figure()
    for t in stack:
        name = t.tranche.name
        fig.add_trace(
            go.Scatter(x=years, y=[t.amount] + yearly[f"balance_{name}"].tolist(), name=name,
                       stackgroup="debt", mode="lines")
        )
    fig.add_trace(
        go.Scatter(x=years, y=[0.0] + yearly["cash_closing"].tolist(), name="Cash",
                   stackgroup="debt", mode="lines", line=dict(dash="dot"))
    )
    fig.update_layout(template="plotly_white", height=380, title="Debt balances by tranche (stacked) + cash",
                      yaxis_title="Balance (m)", xaxis_title="Year")
    return fig


def attribution_chart(attribution: dict) -> go.Figure:
    labels = ["EBITDA growth", "Multiple expansion", "Debt paydown & cash", "Fees", "Total profit"]
    values = [
        attribution["ebitda_growth"], attribution["multiple_expansion"],
        attribution["debt_paydown"], attribution["fees"], attribution["total_profit"],
    ]
    fig = go.Figure(
        go.Bar(x=labels, y=values, marker_color=["#1f4e79", "#4e79a7", "#59a14f", "#e15759", "#f2a900"])
    )
    fig.update_layout(template="plotly_white", height=360, title="Value-creation bridge (equity profit, m)",
                      yaxis_title="m")
    return fig


def covenant_chart(yearly: pd.DataFrame) -> go.Figure:
    fig = make_subplots(cols=2, subplot_titles=("Interest coverage vs 2.0x minimum",
                                                "Net leverage vs stepped covenant"))
    fig.add_trace(go.Scatter(x=yearly["year"], y=yearly["coverage_ratio"], name="Coverage",
                             mode="lines+markers", line=dict(color="#1f4e79")), row=1, col=1)
    fig.add_trace(go.Scatter(x=yearly["year"], y=yearly["coverage_covenant"], name="Minimum",
                             mode="lines", line=dict(color="#c00000", dash="dash")), row=1, col=1)
    fig.add_trace(go.Scatter(x=yearly["year"], y=yearly["leverage_ratio"], name="Net leverage",
                             mode="lines+markers", line=dict(color="#1f4e79")), row=1, col=2)
    fig.add_trace(go.Scatter(x=yearly["year"], y=yearly["leverage_covenant"], name="Covenant",
                             mode="lines", line=dict(color="#c00000", dash="dash")), row=1, col=2)
    fig.update_layout(template="plotly_white", height=360, title="Covenant headroom by year",
                      showlegend=False)
    return fig


def mc_histogram(mc: MCResult) -> go.Figure:
    fig = go.Figure(go.Histogram(x=mc.irrs, nbinsx=60, marker_color="#1f4e79", opacity=0.85))
    for value, label, color in [(mc.p5, "P5", "#c00000"), (mc.median, "Median", "#f2a900"), (mc.p95, "P95", "#59a14f")]:
        if np.isfinite(value):
            fig.add_vline(x=value, line=dict(color=color, dash="dash"),
                          annotation_text=f"{label} {value:.0%}", annotation_position="top")
    fig.update_layout(template="plotly_white", height=420,
                      title=f"IRR distribution over {mc.n:,} simulations (surviving draws; wiped equity at −100%)",
                      xaxis_title="IRR", yaxis_title="Frequency", xaxis_tickformat=".0%")
    return fig


# ---------------------------------------------------------------------------
# Page assembly.
# ---------------------------------------------------------------------------


def main() -> None:
    st.title("StackOptimal")
    st.caption("LBO capital-structure optimization: maximize sponsor IRR subject to covenant "
               "compliance and stressed-downside survival. UK/European leveraged-finance conventions.")

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
            st.error(str(exc))
            return

    company: CompanyData = st.session_state.company

    if company.warnings:
        st.warning("**Data-quality warnings** — every fallback is disclosed:\n\n" +
                   "\n".join(f"- {w}" for w in company.warnings))

    m_entry = company.ev_ebitda + assumptions.entry_premium_turns
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Company", company.name[:24])
    c2.metric("Currency", company.currency)
    c3.metric("Entry EBITDA", f"{company.ebitda:,.0f}m")
    c4.metric("Entry multiple", f"{m_entry:.1f}x")
    c5.metric("Implied EV", f"{m_entry * company.ebitda:,.0f}m")

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
                        naive_mc = monte_carlo(company, assumptions, opt.naive.stack,
                                               n=FRONTIER_MC_DRAWS)
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
        st.info("Fetch a company, review the assumptions in the sidebar, then press **Run optimization**.")
        return

    res = st.session_state.results
    opt: OptimizationResult = res["opt"]

    # ---------------- Headline ----------------
    if opt.optimum is None:
        st.error("**No admissible capital structure.** No leverage × mix combination is feasible in the "
                 "base case AND survives the stressed downside. The memo tab identifies the binding constraint.")
    else:
        o = opt.optimum
        senior_turns = sum(t.turns for t in o.stack if t.tranche.seniority == 1)
        junior_turns = o.leverage - senior_turns
        st.markdown(
            f"<p style='font-size:26px; font-weight:600; line-height:1.35'>"
            f"The optimal structure for {company.name} is <b>{o.leverage:.2f}x</b> leverage in a "
            f"<b>{senior_turns:.1f}/{junior_turns:.1f}</b> senior/junior mix, delivering a base-case IRR of "
            f"<b>{o.base.irr:.1%}</b> ({o.base.moic:.2f}x MOIC), and survives a stressed downside "
            f"(−{assumptions.stress_growth_haircut:.0%} growth, −{assumptions.stress_exit_multiple_shock:.1f}x exit) "
            f"without breaching covenants.</p>",
            unsafe_allow_html=True,
        )
        if opt.on_boundary:
            st.info(f"Grid-boundary disclosure: {opt.boundary_note}")
        if opt.naive is not None and opt.naive_gap_irr is not None and opt.naive_gap_irr > 1e-9:
            st.markdown(
                f"The naive max-IRR structure (**{opt.naive.leverage:.2f}x** at {opt.naive.base.irr:.1%}) "
                f"**fails the stressed downside** — survivability costs {opt.naive_gap_irr:.1%} of headline IRR."
            )

    if opt.optimum is None:
        with st.tabs(["IC Memo"])[0]:
            st.markdown(res["memo"])  # the memo names the binding constraint
            st.download_button("Download memo (markdown)", res["memo"], "ic_memo.md")
        return

    # ---------------- Centerpiece + tabs ----------------
    st.plotly_chart(
        frontier_chart(res["frontier"], res["opt_mc"], res["naive_mc"], _survivable_boundary(opt, res)),
        use_container_width=True,
    )
    st.caption(f"Frontier traced with {FRONTIER_MC_DRAWS:,} Monte Carlo draws per leverage level at the "
               f"optimum's senior mix; the optimum itself uses {assumptions.mc_draws:,} draws. "
               "Common random numbers (fixed seed) make the curve comparable across levels.")

    tab_heat, tab_struct, tab_mc, tab_memo = st.tabs(
        ["Leverage × mix grid", "Optimal structure", "Monte Carlo", "IC Memo"]
    )

    with tab_heat:
        st.plotly_chart(heatmap_chart(opt), use_container_width=True)

    with tab_struct:
        o = opt.optimum
        left, right = st.columns(2)
        left.metric("Blended cost of debt", f"{blended_rate(o.stack, assumptions.base_rate):.2%}")
        right.metric("Equity cheque", f"{o.base.entry['equity']:,.0f}m")
        st.plotly_chart(balances_chart(o.base.yearly, o.stack), use_container_width=True)
        st.plotly_chart(attribution_chart(o.base.attribution), use_container_width=True)
        st.plotly_chart(covenant_chart(o.base.yearly), use_container_width=True)
        st.subheader("Yearly waterfall")
        show = o.base.yearly.copy()
        money_cols = [c for c in show.columns if c not in ("year", "covenant_breach")
                      and not c.startswith(("coverage", "leverage"))]
        show[money_cols] = show[money_cols].round(1)
        st.dataframe(show, use_container_width=True, hide_index=True)

    with tab_mc:
        mc: MCResult = res["opt_mc"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("P(covenant breach)", f"{mc.p_breach:.1%}")
        m2.metric("P(equity wipeout)", f"{mc.p_wipeout:.1%}")
        m3.metric("P(distress)", f"{mc.p_distress:.1%}")
        m4.metric("P(IRR < 0 | survival)", f"{mc.p_negative_irr:.1%}")
        st.plotly_chart(mc_histogram(mc), use_container_width=True)
        st.subheader("Base vs stressed downside")
        stress = opt.optimum.stress
        base = opt.optimum.base
        comp = pd.DataFrame(
            {
                "Base case": [f"{base.irr:.1%}", f"{base.moic:.2f}x", "Yes",
                              f"{base.yearly['coverage_ratio'].min():.2f}x",
                              f"{base.yearly['leverage_ratio'].max():.2f}x"],
                "Stressed case": [
                    f"{stress.irr:.1%}" if stress.feasible and stress.irr is not None else "n/a",
                    f"{stress.moic:.2f}x" if stress.feasible and stress.moic is not None else "0.00x (wiped)"
                    if stress.equity_wiped else "n/a",
                    "Yes" if stress.feasible else f"No — {stress.failure_reason}",
                    f"{stress.yearly['coverage_ratio'].min():.2f}x" if not stress.yearly.empty else "n/a",
                    f"{stress.yearly['leverage_ratio'].max():.2f}x" if not stress.yearly.empty else "n/a",
                ],
            },
            index=["IRR", "MOIC", "Covenant-compliant", "Min interest coverage", "Max net leverage"],
        )
        st.table(comp)

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
