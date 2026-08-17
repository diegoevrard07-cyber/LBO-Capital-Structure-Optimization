"""Monte Carlo risk analysis and the efficient frontier.

The deterministic engine answers "what happens under THIS scenario?"; the IC's
real question is "what is the DISTRIBUTION of outcomes, and how often do we
lose the company?". This module perturbs the three inputs that dominate LBO
risk — operating growth, the exit multiple, and the floating base-rate path —
and re-runs the full engine per draw.

On vectorization: the waterfall is path-dependent (each year's sweep sets the
next year's interest), so the per-draw simulation cannot be honestly
vectorized across years. We vectorize what is safe — all random draws are
generated up front in one numpy call — and loop the engine per draw.
Correctness before speed: 5,000 draws x 5 years is a few seconds, and a
research tool that is exactly reproducible (fixed seed) and exactly right is
worth more than a fast approximation.

Reproducibility: every entry point takes an explicit seed. A research tool
must return identical numbers run-to-run, or its outputs cannot be cited in
an IC memo, a regression test, or a README case study.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .debt import SizedTranche, build_stack
from .inputs import CompanyData, MarketAssumptions
from .model import MIN_VIABLE_EXIT_MULTIPLE, LBOResult, OperatingCase, run_lbo

# Sane truncation bounds for the random draws. Gaussian tails are unbounded;
# real economies are not. Bounds are wide enough to include genuinely bad
# outcomes while excluding absurd ones that would dominate the tail stats.
GROWTH_FLOOR = -0.10  # a 10%/yr five-year decline is a melting ice cube, not an LBO
GROWTH_CEIL = 0.15  # consistent with the underwriting cap in inputs.py
MULTIPLE_CEIL_TURNS = 3.0  # allow up to +3 turns of expansion vs entry — a full cycle re-rating
RATE_FLOOR = 0.0  # no negative policy rates in this analysis
RATE_CEIL = 0.10  # 10% base rate: beyond the 2022-23 hiking peak — a genuine tail event


@dataclass
class MCResult:
    """Monte Carlo output for ONE capital structure.

    IRR statistics are computed over draws that survive to exit (feasible);
    wiped equity contributes IRR = -100%. Distressed draws (covenant breach /
    cash shortfall mid-hold) have no meaningful IRR and are reported through
    the distress probabilities instead — survivorship bias would otherwise
    flatter the distribution. ``p_negative_irr`` is therefore conditional on
    survival; the unconditional probability of losing money is approximately
    p_distress + (1 - p_distress) * p_negative_irr.
    """

    n: int
    seed: int
    leverage: float
    mix: float
    irrs: np.ndarray  # IRR per surviving draw (-1.0 for wiped equity)
    p_breach: float  # P(covenant breach or cash shortfall mid-hold)
    p_wipeout: float  # P(equity worth nothing at exit | survived to exit) * P(survive)
    p_distress: float  # P(breach OR wipeout) — the frontier's x-axis
    p_negative_irr: float  # P(IRR < 0 | survived to exit)
    p5: float
    median: float
    p95: float
    n_distress: int


def _simulate_draws(
    company: CompanyData,
    a: MarketAssumptions,
    stack: list[SizedTranche],
    n: int,
    seed: int,
) -> tuple[list[LBOResult], np.ndarray, np.ndarray, np.ndarray]:
    """Generate all draws up front (vectorized), then run the engine per draw."""
    rng = np.random.default_rng(seed)
    m_entry = company.ev_ebitda + a.entry_premium_turns
    m_exit_base = m_entry + a.exit_multiple_premium

    growths = np.clip(rng.normal(company.growth, a.mc_growth_sigma, n), GROWTH_FLOOR, GROWTH_CEIL)
    exit_multiples = np.clip(
        rng.normal(m_exit_base, a.mc_multiple_sigma, n),
        MIN_VIABLE_EXIT_MULTIPLE,
        m_entry + MULTIPLE_CEIL_TURNS,
    )
    # One persistent rate shift per draw: policy rates are highly autocorrelated,
    # so a parallel shift of the whole path is more realistic than iid yearly noise.
    rate_shifts = np.clip(
        rng.normal(0.0, a.mc_rate_sigma, n),
        RATE_FLOOR - a.base_rate,
        RATE_CEIL - a.base_rate,
    )

    results: list[LBOResult] = []
    for i in range(n):
        case = OperatingCase(
            growth=float(growths[i]),
            exit_multiple_shock=m_exit_base - float(exit_multiples[i]),
            label="MC",
        )
        rate_path = [a.base_rate + float(rate_shifts[i])] * a.hold_years
        results.append(run_lbo(company, a, stack, case, rate_path=rate_path))
    return results, growths, exit_multiples, rate_shifts


def monte_carlo(
    company: CompanyData,
    a: MarketAssumptions,
    stack: list[SizedTranche],
    n: int | None = None,
    seed: int | None = None,
) -> MCResult:
    """Run ``n`` perturbed simulations of one structure and summarize the risk."""
    n = n or a.mc_draws
    seed = a.mc_seed if seed is None else seed
    results, _, _, _ = _simulate_draws(company, a, stack, n, seed)

    total_debt_turns = sum(t.turns for t in stack)
    senior_turns = sum(t.turns for t in stack if t.tranche.seniority == 1)
    mix = senior_turns / total_debt_turns if total_debt_turns > 0 else 1.0

    n_breach = sum(1 for r in results if not r.feasible)
    n_wipe = sum(1 for r in results if r.feasible and r.equity_wiped)
    survivors = [r for r in results if r.feasible]
    irrs = np.array([-1.0 if r.equity_wiped else r.irr for r in survivors], dtype=float)

    if irrs.size:
        p5, median, p95 = np.percentile(irrs, [5, 50, 95])
        p_negative = float(np.mean(irrs < 0))
    else:  # every draw distressed — the structure is simply unownable
        p5 = median = p95 = float("nan")
        p_negative = float("nan")

    return MCResult(
        n=n,
        seed=seed,
        leverage=total_debt_turns,
        mix=mix,
        irrs=irrs,
        p_breach=n_breach / n,
        p_wipeout=n_wipe / n,
        p_distress=(n_breach + n_wipe) / n,
        p_negative_irr=p_negative,
        p5=float(p5),
        median=float(median),
        p95=float(p95),
        n_distress=n_breach + n_wipe,
    )


def frontier(
    company: CompanyData,
    a: MarketAssumptions,
    mix: float | None = None,
    n: int = 2000,
    seed: int | None = None,
    step: float = 0.5,
) -> pd.DataFrame:
    """Median IRR vs P(distress) across leverage — the centerpiece chart.

    Leverage is swept at a fixed senior mix (the optimum's mix by default).
    ``n`` defaults below the full MC draw count because the frontier needs one
    MC per leverage level; 2,000 draws keep P(distress) stable to ~1pp while
    staying interactive. Common random numbers (same seed at every level) make
    the traced curve smooth and its monotonicity exact rather than noisy.
    """
    if mix is None:
        from .optimizer import optimize  # local import: optimizer never needs risk

        opt = optimize(company, a)
        mix = opt.optimum.mix if opt.optimum else 1.0

    seed = a.mc_seed if seed is None else seed
    max_capacity = a.senior_cap * mix + a.second_lien_cap + a.mezz_cap
    levels = np.arange(2.0, max_capacity + 1e-9, step)

    rows = []
    for lev in levels:
        stack = build_stack(float(lev), mix, company.ebitda, a)
        mc = monte_carlo(company, a, stack, n=n, seed=seed)
        rows.append(
            {
                "leverage": float(lev),
                "median_irr": mc.median,
                "p_distress": mc.p_distress,
                "p_breach": mc.p_breach,
                "p_wipeout": mc.p_wipeout,
                "p5": mc.p5,
                "p95": mc.p95,
            }
        )
    return pd.DataFrame(rows)
