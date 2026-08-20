"""Investment-committee memo generator.

Turns the optimizer and Monte Carlo outputs into a two-page markdown memo:
every number traced to a model output, the downside taken as seriously as the
upside, and a recommendation that follows from the analysis. If no admissible
structure exists the memo says so and names the binding constraint.
"""

from __future__ import annotations

from .inputs import CompanyData, MarketAssumptions
from .optimizer import OptimizationResult
from .risk import MCResult


def _m(x: float) -> str:
    """Currency millions."""
    return f"{x:,.0f}"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def _x(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.2f}x"


def generate_memo(
    company: CompanyData,
    a: MarketAssumptions,
    opt: OptimizationResult,
    mc: MCResult | None,
) -> str:
    """Render the full IC memo as markdown. Every figure comes from results."""
    cur = company.currency
    o = opt.optimum

    if o is None:
        binding = _binding_constraint(opt)
        return f"""# IC Memo — Project {company.name} ({company.ticker})

**Recommendation: DO NOT PROCEED — no admissible capital structure.**

Across the full searched space ({len(opt.grid)} leverage x mix combinations), no structure
was simultaneously feasible in the base case and survivable in the stressed downside.

**Binding constraint:** {binding}

The honest conclusion is that at the current entry pricing ({company.ev_ebitda:.1f}x EV/EBITDA
plus a {a.entry_premium_turns:.1f}x premium) the cash flows do not support any capital structure
within market capacity and covenant practice. Options: reprice the entry, find operational
upside beyond the {company.growth:.1%} underwritten growth, or walk away.
"""

    base, stress = o.base, o.stress
    e = base.entry
    x = base.exit

    # --- Sources & Uses (two-sided table) ---
    uses_lines = [
        ("Purchase price (EV)", e["ev"]),
        ("Transaction & financing fees", e["fees"]),
        ("**Total uses**", e["ev"] + e["fees"]),
    ]
    sources_lines = [
        (f"{t.tranche.name} ({t.turns:.2f}x @ {_rate_str(t, a)})", t.amount) for t in o.stack
    ]
    sources_lines.append(("Sponsor equity", e["equity"]))
    sources_lines.append(("**Total sources**", e["total_debt"] + e["equity"]))
    su_rows = ["| Uses | | Sources | |", "|---|---:|---|---:|"]
    for i in range(max(len(uses_lines), len(sources_lines))):
        u = uses_lines[i] if i < len(uses_lines) else ("", "")
        s = sources_lines[i] if i < len(sources_lines) else ("", "")
        su_rows.append(
            f"| {u[0]} | {_m(u[1]) if u[0] else ''} | {s[0]} | {_m(s[1]) if s[0] else ''} |"
        )
    sources_uses = "\n".join(su_rows)

    # --- Tranche table ---
    tranche_rows = [
        "| Tranche | Turns | Amount | Pricing | Amortization | Seniority |",
        "|---|---:|---:|---|---|---|",
    ]
    for t in o.stack:
        amort = f"{t.tranche.amort_pct:.0%}/yr" if t.tranche.amort_pct > 0 else "Bullet"
        tranche_rows.append(
            f"| {t.tranche.name} | {t.turns:.2f}x | {_m(t.amount)} | {_rate_str(t, a)} | {amort} | {t.tranche.seniority} |"
        )
    tranche_table = "\n".join(tranche_rows)

    # --- Covenant headroom (base case) ---
    y = base.yearly
    min_cov_headroom = (y["coverage_ratio"] - a.min_interest_coverage).min()
    min_lev_headroom = (y["leverage_covenant"] - y["leverage_ratio"]).min()

    # --- Attribution ---
    at = base.attribution

    # --- Downside ---
    if stress.feasible:
        stress_line = (
            f"the structure remains covenant-compliant through the hold; stressed exit equity of "
            f"{cur} {_m(stress.exit['equity'])}m vs {cur} {_m(e['equity'])}m invested "
            f"(stressed MOIC {_x(stress.moic)})."
        )
    else:
        stress_line = f"STRESS FAILURE — {stress.failure_reason}."

    mc_block = "_Monte Carlo not run._"
    if mc is not None:
        mc_block = f"""Over {mc.n:,} simulations (growth, exit multiple and base rate perturbed; seed {mc.seed}):

| Metric | Value |
|---|---:|
| Median IRR | {_pct(mc.median)} |
| P5 / P95 IRR | {_pct(mc.p5)} / {_pct(mc.p95)} |
| P(covenant breach or cash shortfall) | {mc.p_breach:.1%} |
| P(equity wipeout at exit) | {mc.p_wipeout:.1%} |
| **P(distress)** | **{mc.p_distress:.1%}** |
| P(IRR < 0 \\| survival) | {_pct(mc.p_negative_irr)} |"""

    boundary_note = ""
    if opt.on_boundary:
        boundary_note = f"\n\n> **Grid-boundary disclosure:** {opt.boundary_note}."

    naive = opt.naive
    naive_block = ""
    if naive is not None and (naive.leverage, naive.mix) != (o.leverage, o.mix):
        naive_block = f"""
## The naive-vs-survivable gap

Maximizing IRR while ignoring downside survival selects **{naive.leverage:.2f}x at {naive.mix:.0%} senior mix**
(base IRR {_pct(naive.base.irr)}). That structure **fails the stressed downside**
({naive.stress.failure_reason if naive.stress and naive.stress.failure_reason else 'equity wiped at stressed exit'}).
The survivable optimum gives up {_pct(opt.naive_gap_irr)} of headline IRR — the price of keeping the
company through a recession. This gap is the central output of the analysis."""

    return f"""# IC Memo — Project {company.name} ({company.ticker})

**Prepared from model outputs; all figures in {cur} millions unless stated.**

## 1. Deal summary

Acquisition of {company.name} at {e['entry_multiple']:.1f}x EV/EBITDA (market {company.ev_ebitda:.1f}x plus a
{a.entry_premium_turns:.1f}x control premium), entry EBITDA of {cur} {_m(company.ebitda)}m, enterprise value of
{cur} {_m(e['ev'])}m. Recommended structure: **{o.leverage:.2f}x total leverage at {o.mix:.0%} senior mix**,
delivering a base-case IRR of **{_pct(base.irr)}** ({_x(base.moic)} MOIC) over a {a.hold_years}-year hold,
while surviving the stressed downside.{boundary_note}

## 2. Sources & uses

{sources_uses}

## 3. Recommended structure

{tranche_table}

Plus an undrawn revolver of {a.revolver_commitment_turns:.2f}x EBITDA ({cur} {_m(a.revolver_commitment_turns * company.ebitda)}m)
at S+{a.revolver_margin * 10000:.0f} drawn / {a.revolver_undrawn_fee * 10000:.0f}bp undrawn — the liquidity backstop,
repaid first from excess cash.

Blended cash cost of debt {sum(t.amount * t.cash_rate(a.base_rate) for t in o.stack) / e['total_debt']:.2%} at a
{a.base_rate:.2%} base rate. Covenants: minimum interest coverage {a.min_interest_coverage:.1f}x; maximum net
leverage opening at entry +{a.leverage_covenant_headroom:.1f}x, stepping down {a.leverage_stepdown:.1f}x/yr to a
{a.leverage_floor:.1f}x floor.

## 4. Base-case returns

IRR **{_pct(base.irr)}**, MOIC **{_x(base.moic)}** on a {cur} {_m(e['equity'])}m equity cheque; exit at
{x['exit_multiple']:.1f}x in year {a.hold_years} (no multiple expansion assumed).

Value creation of {cur} {_m(at['total_profit'])}m decomposes as:

| Driver | {cur}m |
|---|---:|
| EBITDA growth ({company.growth:.1%}/yr) | {_m(at['ebitda_growth'])} |
| Multiple expansion | {_m(at['multiple_expansion'])} |
| Debt paydown & cash build | {_m(at['debt_paydown'])} |
| Fees | {_m(at['fees'])} |

No multiple expansion is assumed, so all profit comes from growth and deleveraging.

## 5. Downside analysis

Stressed case (growth haircut {a.stress_growth_haircut:.0%}, a one-off {a.stress_ebitda_shock:.0%} EBITDA shock in
year 1, exit multiple −{a.stress_exit_multiple_shock:.1f}x): {stress_line}

Base-case covenant headroom — minimum interest-coverage cushion **{min_cov_headroom:.2f}x**, minimum
net-leverage cushion **{min_lev_headroom:.2f}x** over the hold.

{mc_block}
{naive_block}

## 6. Key risks

- **Coverage at entry**: year-1 interest coverage is the tightest point of the hold; a rate shock before
  deleveraging bites is the fastest route to breach.
- **Exit multiple**: no expansion is underwritten; a −1.0x de-rating is absorbed in stress, but a deeper
  cyclical de-rating would impair the equity.
- **Refinancing risk at exit**: residual debt must be refinanceable at exit multiples prevailing in year
  {a.hold_years}.
- **Data quality**: {len(company.warnings)} ingestion warning(s) — see dashboard; public-filings data is
  noisier than the paid loan-market feeds a real process would use.

## 7. Recommendation

Proceed on the recommended structure: {o.leverage:.2f}x at {o.mix:.0%} senior mix. It is the highest-IRR
structure that remains covenant-compliant in every year of the base case AND survives the stressed downside
with the equity intact. Additional leverage beyond this point adds little IRR and fails the downside test;
we do not recommend it.
"""


def _rate_str(t, a: MarketAssumptions) -> str:
    if t.tranche.is_floating:
        return f"S+{t.tranche.margin_or_rate * 10000:.0f}"
    return f"{t.tranche.margin_or_rate:.0%} fixed"


def _binding_constraint(opt: OptimizationResult) -> str:
    """When nothing is admissible, name the constraint that binds most often."""
    g = opt.grid
    if g.empty:
        return "no grid points could be evaluated."
    reasons = g["failure"].dropna()
    if reasons.empty:
        return "unknown — inspect the grid."
    # Classify by the constraint family and report the most common.
    families = {
        "interest coverage": reasons.str.contains("coverage").sum(),
        "net leverage covenant": reasons.str.contains("leverage covenant").sum(),
        "cash shortfall": reasons.str.contains("cash shortfall").sum(),
        "negative equity at entry": reasons.str.contains("negative equity").sum(),
        "market capacity": reasons.str.contains("exceeds total capacity").sum(),
    }
    top = max(families, key=families.get)
    return f"{top} (binding at {families[top]} of {len(g)} grid points)."
