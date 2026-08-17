"""Grid-search optimizer over the capital-structure space.

SOLVER JUSTIFICATION (reused verbatim in the README): the debt waterfall
makes the objective non-convex and path-dependent — covenant tests kink the
feasible region, tranche-cap boundaries kink the cost of funds, and the cash
sweep switches the mapping from this year's free cash flow to next year's
interest bill. Gradient methods on such a surface silently converge to local
optima and say nothing about the surrounding terrain. A transparent grid scan
finds the global optimum over the discretized space, costs milliseconds per
evaluation, and yields the full risk-return surface as a free by-product.

The decision space is two-dimensional: TOTAL LEVERAGE (turns of EBITDA) x
SENIOR MIX (how much of the raise is placed in cheap senior paper before
spilling into junior tranches). A structure is ADMISSIBLE iff it is feasible
in the base case AND survives the stressed downside. The optimizer reports
the admissible structure with the highest base-case IRR — and, separately,
the naive unconstrained max-IRR structure, because the gap between the two
is the headline insight: leverage you cannot survive is not leverage you own.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .debt import SizedTranche, blended_rate, build_stack
from .inputs import CompanyData, MarketAssumptions
from .model import LBOResult, base_case, run_lbo, run_stress

# Grid definition. 0.25x leverage steps and 5pp mix steps are fine enough that
# the optimum is economically indistinguishable from any finer grid, while
# keeping the full scan (with stress runs) comfortably interactive.
LEVERAGE_MIN = 2.0  # below 2x a deal is barely an LBO
LEVERAGE_STEP = 0.25
MIX_MIN = 0.5  # below 50% senior the structure is junior-dominated — not a bankable TLB deal
MIX_STEP = 0.05


@dataclass
class GridPoint:
    """One evaluated capital structure."""

    leverage: float
    mix: float
    stack: list[SizedTranche] | None
    base: LBOResult | None
    stress: LBOResult | None
    stress_survives: bool
    admissible: bool
    failure: str | None  # why the point is inadmissible, in plain language


@dataclass
class OptimizationResult:
    """The full scan plus its two headline structures."""

    grid: pd.DataFrame  # tidy frame of EVERY grid point, feasible or not
    optimum: GridPoint | None  # max base IRR among admissible structures
    naive: GridPoint | None  # max base IRR ignoring downside survival
    naive_gap_irr: float | None  # naive IRR - optimum IRR: the price of survivability
    on_boundary: bool  # True if the optimum sits on a grid edge
    boundary_note: str | None
    leverage_levels: list[float]
    mixes: list[float]


def optimize(company: CompanyData, a: MarketAssumptions) -> OptimizationResult:
    """Scan leverage x mix, run base + stress at each point, pick the optimum."""
    max_capacity = a.senior_cap + a.second_lien_cap + a.mezz_cap  # at mix = 1.0
    # Round to kill float dust from arange (1.0000000000000004 would otherwise
    # be a phantom grid point distinct from 1.0).
    leverage_levels = [round(float(x), 10) for x in np.arange(LEVERAGE_MIN, max_capacity + 1e-9, LEVERAGE_STEP)]
    mixes = [round(float(x), 10) for x in np.arange(MIX_MIN, 1.0 + 1e-9, MIX_STEP)]

    points: list[GridPoint] = []
    for leverage in leverage_levels:
        for mix in mixes:
            try:
                stack = build_stack(float(leverage), float(mix), company.ebitda, a)
            except ValueError as exc:
                points.append(GridPoint(leverage, mix, None, None, None, False, False, str(exc)))
                continue

            base = run_lbo(company, a, stack, base_case(company))
            if not base.feasible:
                points.append(GridPoint(leverage, mix, stack, base, None, False, False, base.failure_reason))
                continue

            stress, survives = run_stress(company, a, stack)
            admissible = survives  # base is feasible here; admissible = feasible AND survives
            failure = None if admissible else f"stress failure: {stress.failure_reason or 'equity wiped at stressed exit'}"
            points.append(GridPoint(leverage, mix, stack, base, stress, survives, admissible, failure))

    grid = pd.DataFrame(
        [
            {
                "leverage": p.leverage,
                "mix": p.mix,
                "blended_rate": blended_rate(p.stack, a.base_rate) if p.stack else np.nan,
                "base_irr": p.base.irr if p.base and p.base.feasible else np.nan,
                "base_moic": p.base.moic if p.base and p.base.feasible else np.nan,
                "base_feasible": bool(p.base and p.base.feasible),
                "base_failure": p.base.failure_reason if p.base else p.failure,
                "stress_irr": p.stress.irr if p.stress and p.stress.feasible else np.nan,
                "stress_survives": p.stress_survives,
                "admissible": p.admissible,
                "failure": p.failure,
            }
            for p in points
        ]
    )

    feasible_points = [p for p in points if p.base is not None and p.base.feasible and p.base.irr is not None]
    admissible_points = [p for p in feasible_points if p.admissible]

    naive = max(feasible_points, key=lambda p: p.base.irr) if feasible_points else None
    optimum = max(admissible_points, key=lambda p: p.base.irr) if admissible_points else None
    naive_gap = (naive.base.irr - optimum.base.irr) if (naive and optimum) else None

    on_boundary, boundary_note = _boundary_check(optimum, leverage_levels, mixes)

    return OptimizationResult(
        grid=grid,
        optimum=optimum,
        naive=naive,
        naive_gap_irr=naive_gap,
        on_boundary=on_boundary,
        boundary_note=boundary_note,
        leverage_levels=leverage_levels,
        mixes=mixes,
    )


def _boundary_check(
    optimum: GridPoint | None,
    leverage_levels: list[float],
    mixes: list[float],
) -> tuple[bool, str | None]:
    """Flag an optimum sitting on the edge of the searched space.

    A boundary optimum means the TRUE optimum may lie outside the grid, and
    hiding that would overstate the answer. We surface it plainly. Note the
    asymmetry we disclose honestly: the optimum almost always sits at 100%
    senior mix because senior debt is strictly cheaper — that edge is
    economically expected, whereas a leverage-edge optimum would be a red flag.
    """
    if optimum is None:
        return False, None
    notes = []
    if optimum.leverage >= max(leverage_levels) - 1e-9:
        notes.append(
            f"optimum leverage {optimum.leverage:.2f}x is at the top of the searched range — "
            "the unconstrained optimum may be higher; widen the grid before trusting this"
        )
    if optimum.mix >= max(mixes) - 1e-9:
        notes.append(
            "optimum is at 100% senior mix — economically expected (senior debt is strictly "
            "cheaper), but it is a grid edge"
        )
    elif optimum.mix <= min(mixes) + 1e-9:
        notes.append("optimum is at the minimum senior mix — a grid edge worth investigating")
    return bool(notes), "; ".join(notes) if notes else None
