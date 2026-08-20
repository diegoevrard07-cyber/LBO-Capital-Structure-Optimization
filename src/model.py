"""The deterministic LBO engine.

This is the core of the tool. Given a target, a sized debt stack, and an
operating case, it simulates the deal year by year over the hold and returns
everything an investment committee needs: returns, feasibility, covenant
headroom, the full debt waterfall, and a value-creation bridge.

Why a simulation and not a formula: the debt waterfall is PATH-DEPENDENT.
This year's sweep determines next year's opening balances, hence next year's
interest, hence next year's taxes (interest is deductible), hence next year's
free cash flow available to sweep. Covenant tests kink the feasible region,
and tranche caps kink the cost of funds. No closed form survives those kinks,
so we compute year by year — which is also exactly how a deal team builds it
in Excel, and therefore easy to audit line by line.

Conventions (UK/European): interest is cash-pay on OPENING balances — the
beginning-of-period convention, applied identically to every tranche and the
revolver. This deliberately avoids the interest<->sweep circularity (interest
on average balances would need the closing balance, which needs the sweep,
which needs interest): it is the standard clean convention, stated up front,
slightly conservative because the year's paydown earns no intra-year interest
relief. Interest and the amortization of capitalized financing fees are
tax-deductible; D&A is set equal to capex (steady-state: the asset base is
held roughly constant, a standard underwriting shortcut); mandatory amort is
a percentage of ORIGINAL face; the cash sweep applies to the YEAR's excess
cash flow (not the accumulated cash balance) and prepays in strict seniority.
A revolver, undrawn at close, bridges cash shortfalls and is repaid first —
a shortfall is fatal only once cash and the commitment are both exhausted.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .debt import SizedTranche, total_debt
from .inputs import CompanyData, MarketAssumptions

MIN_VIABLE_EXIT_MULTIPLE = (
    0.5  # floor for stressed/MC exit multiples: below 0.5x EBITDA is not a sale, it's a liquidation
)


@dataclass(frozen=True)
class OperatingCase:
    """One deterministic operating scenario.

    ``ebitda_shock_y1`` is a one-off fractional hit to year-1 EBITDA that
    persists in the LEVEL of earnings (a recession resets the base; it does
    not hand the lost profit back in year 2). ``exit_multiple_shock`` is
    subtracted from the exit multiple in turns.
    """

    growth: float
    ebitda_shock_y1: float = 0.0
    exit_multiple_shock: float = 0.0
    label: str = "Base"


def base_case(company: CompanyData) -> OperatingCase:
    """The underwritten plan: management-consistent growth, exit at entry multiple."""
    return OperatingCase(growth=company.growth, label="Base")


def stress_case(company: CompanyData, a: MarketAssumptions) -> OperatingCase:
    """The downside every structure must survive: a recession year, slower
    growth thereafter, and a de-rated exit."""
    return OperatingCase(
        growth=company.growth * (1.0 - a.stress_growth_haircut),
        ebitda_shock_y1=a.stress_ebitda_shock,
        exit_multiple_shock=a.stress_exit_multiple_shock,
        label="Stress",
    )


@dataclass
class LBOResult:
    """Everything a single run produces. ``failure_reason`` is always a
    specific, human-readable sentence — never a bare NaN — because the UI and
    the IC memo both surface WHY a structure dies, not just that it dies."""

    irr: float | None
    moic: float | None
    feasible: bool
    failure_reason: str | None
    equity_wiped: bool
    yearly: pd.DataFrame  # one row per projection year; empty if the deal fails at entry
    entry: dict  # multiples, EV, fees, debt, equity cheque at close
    exit: dict  # multiples, EV, net debt, equity value at exit
    attribution: dict  # value-creation bridge: growth / multiple / paydown / fees
    case_label: str = "Base"


def _irr(cashflows: list[float]) -> float | None:
    """Internal rate of return via the polynomial roots of the NPV equation.

    With x = 1/(1+r), NPV = sum(c_t * x**t) is a polynomial in x, so numpy's
    root finder gives every solution. With a single sign change (our no-recap
    vector [-E0, 0, ..., 0, E_T]) there is exactly one positive real root and
    the answer is unique. The vector is recap-ready: interim distributions
    introduce further sign changes, in which case we follow market convention
    and take the root nearest a plausible hurdle (~20%).
    """
    if not cashflows or cashflows[0] >= 0:
        return None
    coeffs = list(reversed(cashflows))  # np.roots wants highest degree first
    roots = np.roots(coeffs)
    real_positive = [r.real for r in roots if abs(r.imag) < 1e-9 and r.real > 0]
    if not real_positive:
        return None
    rates = [1.0 / x - 1.0 for x in real_positive]
    rates = [r for r in rates if r > -0.9999]
    if not rates:
        return None
    return float(min(rates, key=lambda r: abs(r - 0.20)))


def run_lbo(
    company: CompanyData,
    a: MarketAssumptions,
    stack: list[SizedTranche],
    case: OperatingCase,
    rate_path: list[float] | None = None,
    check_covenants: bool = True,
) -> LBOResult:
    """Simulate one LBO and return the full result.

    ``rate_path`` optionally overrides the base rate year by year (Monte Carlo
    uses this to stress floating coupons); ``check_covenants=False`` is used
    only by tests isolating pure leverage mechanics.
    """
    n = a.hold_years
    if rate_path is None:
        rate_path = [a.base_rate] * n
    if len(rate_path) != n:
        raise ValueError(f"rate_path must have one entry per hold year ({n}).")

    # ---------------- Entry: sources & uses ----------------
    m_entry = company.ev_ebitda + a.entry_premium_turns
    ev0 = m_entry * company.ebitda
    debt0 = total_debt(stack)
    fees = a.debt_fee_pct * debt0 + a.ev_fee_pct * ev0
    uses = ev0 + fees
    equity0 = uses - debt0

    entry = {
        "entry_multiple": m_entry,
        "ev": ev0,
        "fees": fees,
        "total_debt": debt0,
        "equity": equity0,
        "entry_leverage": debt0 / company.ebitda if company.ebitda > 0 else np.nan,
    }

    def _fail(reason: str, yearly: pd.DataFrame) -> LBOResult:
        return LBOResult(
            irr=None,
            moic=None,
            feasible=False,
            failure_reason=reason,
            equity_wiped=False,
            yearly=yearly,
            entry=entry,
            exit={},
            attribution={},
            case_label=case.label,
        )

    empty = pd.DataFrame()
    if equity0 <= 0:
        return _fail(
            f"negative equity at entry: debt of {debt0:,.0f} exceeds uses of {uses:,.0f} "
            f"(equity cheque {equity0:,.0f})",
            empty,
        )

    # ---------------- Projection + waterfall ----------------
    opening = {t.tranche.name: t.amount for t in stack}
    original_face = {t.tranche.name: t.amount for t in stack}
    tranches = sorted(stack, key=lambda t: t.tranche.seniority)
    cash = 0.0  # no minimum-cash funding at close — documented simplification
    rcf_commitment = a.revolver_commitment_turns * company.ebitda
    rcf_drawn = 0.0  # the revolver is undrawn at close (it is a backstop, not a source)
    # Capitalized financing fees amortize straight-line over the hold as a
    # NON-CASH tax deduction — the deferred-financing-fee treatment. The cash
    # left the door at close (inside the equity cheque); only the tax shield
    # arrives over time.
    financing_fees = a.debt_fee_pct * debt0
    fee_amort = financing_fees / n
    rows: list[dict] = []

    for year in range(1, n + 1):
        g = case.growth
        ebitda = company.ebitda * (1.0 + g) ** year
        if case.ebitda_shock_y1 > 0:
            ebitda *= 1.0 - case.ebitda_shock_y1  # recession resets the earnings base permanently
        revenue = company.revenue * (1.0 + g) ** year
        revenue_prev = company.revenue * (1.0 + g) ** (year - 1)
        capex = company.capex_pct * revenue
        delta_wc = a.wc_pct_of_delta_rev * (revenue - revenue_prev)
        da = capex  # steady-state: D&A = capex keeps the asset base constant

        cash_open = cash
        rcf_open = rcf_drawn
        base_rate = rate_path[year - 1]
        interest = {
            t.tranche.name: opening[t.tranche.name] * t.cash_rate(base_rate) for t in tranches
        }
        # Revolver cost: margin on the OPENING drawn balance (same convention
        # as every tranche) plus the commitment fee on undrawn capacity.
        rcf_interest = rcf_open * (base_rate + a.revolver_margin) + a.revolver_undrawn_fee * (
            rcf_commitment - rcf_open
        )
        total_interest = sum(interest.values()) + rcf_interest

        ebit = ebitda - da
        taxes = max(
            0.0, (ebit - total_interest - fee_amort) * company.tax_rate
        )  # interest and fee amortization are deductible; no tax refunds
        cfads = ebitda - capex - taxes - delta_wc

        # Mandatory amortization on original face, capped at the remaining balance.
        amort = {
            t.tranche.name: min(
                t.tranche.amort_pct * original_face[t.tranche.name], opening[t.tranche.name]
            )
            for t in tranches
        }
        total_amort = sum(amort.values())

        # Excess cash flow: THIS year's cash generation after debt service.
        # The sweep applies to this, not to the accumulated cash balance —
        # matching how credit agreements define an ECF sweep, and making the
        # stated retention (1 - sweep%) actually hold year after year.
        ecf = cfads - total_interest - total_amort

        sweep: dict[str, float] = {t.tranche.name: 0.0 for t in tranches}
        rcf_draw = rcf_repay = 0.0
        if ecf < -1e-12:
            # Shortfall: opening cash absorbs it first, then the revolver
            # draws; only when both are exhausted is it a payment default.
            shortfall = -ecf
            from_cash = min(cash_open, shortfall)
            cash = cash_open - from_cash
            need = shortfall - from_cash
            if need > rcf_commitment - rcf_open + 1e-9:
                closing = {
                    t.tranche.name: opening[t.tranche.name] - amort[t.tranche.name]
                    for t in tranches
                }
                rows.append(
                    _row(
                        year,
                        ebitda,
                        revenue,
                        capex,
                        delta_wc,
                        taxes,
                        cfads,
                        cash_open,
                        interest,
                        amort,
                        sweep,
                        closing,
                        cash,
                        a,
                        rcf_interest=rcf_interest,
                        rcf_draw=0.0,
                        rcf_repay=0.0,
                        rcf_balance=rcf_open,
                    )
                )
                return _fail(
                    f"cash shortfall in year {year}: debt service exceeds cash generated "
                    f"by {shortfall:,.1f}, beyond cash on hand and the remaining "
                    f"{rcf_commitment - rcf_open:,.1f} revolver capacity",
                    pd.DataFrame(rows),
                )
            rcf_draw = need
        else:
            # Surplus: the revolver is repaid FIRST and in full if possible —
            # it is the most senior claim and the cheapest to re-draw — then
            # the sweep share of the remainder prepays term debt senior-first,
            # and the retained share builds balance-sheet cash.
            rcf_repay = min(rcf_open, ecf)
            surplus = ecf - rcf_repay
            remaining = a.cash_sweep_pct * surplus
            for t in tranches:
                name = t.tranche.name
                after_amort = opening[name] - amort[name]
                swept = min(after_amort, max(0.0, remaining))
                sweep[name] = swept
                remaining -= swept
            cash = cash_open + surplus - sum(sweep.values())

        closing = {
            t.tranche.name: opening[t.tranche.name] - amort[t.tranche.name] - sweep[t.tranche.name]
            for t in tranches
        }
        rcf_drawn = rcf_open + rcf_draw - rcf_repay
        opening = closing

        # ---------------- Covenant tests ----------------
        coverage = ebitda / total_interest if total_interest > 1e-12 else np.inf
        net_debt = sum(closing.values()) + rcf_drawn - cash
        leverage = net_debt / ebitda if ebitda > 0 else np.inf
        covenant_cap = max(
            entry["entry_leverage"]
            + a.leverage_covenant_headroom
            - a.leverage_stepdown * (year - 1),
            a.leverage_floor,
        )
        breach: str | None = None
        if check_covenants:
            if coverage < a.min_interest_coverage:
                breach = (
                    f"interest coverage covenant breach in year {year}: "
                    f"{coverage:.2f}x < {a.min_interest_coverage:.2f}x minimum"
                )
            elif leverage > covenant_cap + 1e-9:
                breach = (
                    f"net leverage covenant breach in year {year}: "
                    f"{leverage:.2f}x > {covenant_cap:.2f}x covenant"
                )

        rows.append(
            _row(
                year,
                ebitda,
                revenue,
                capex,
                delta_wc,
                taxes,
                cfads,
                cash_open,
                interest,
                amort,
                sweep,
                closing,
                cash,
                a,
                rcf_interest=rcf_interest,
                rcf_draw=rcf_draw,
                rcf_repay=rcf_repay,
                rcf_balance=rcf_drawn,
                coverage=coverage,
                leverage=leverage,
                covenant_cap=covenant_cap,
                breach=breach,
            )
        )
        if breach is not None:
            return _fail(breach, pd.DataFrame(rows))

    yearly = pd.DataFrame(rows)

    # ---------------- Exit ----------------
    m_exit = max(
        m_entry + a.exit_multiple_premium - case.exit_multiple_shock, MIN_VIABLE_EXIT_MULTIPLE
    )
    ebitda_t = yearly["ebitda"].iloc[-1]
    ev_t = m_exit * ebitda_t
    net_debt_t = yearly["total_debt_closing"].iloc[-1] - yearly["cash_closing"].iloc[-1]
    equity_t = ev_t - net_debt_t
    wiped = equity_t <= 0

    exit_ = {
        "exit_multiple": m_exit,
        "ev": ev_t,
        "net_debt": net_debt_t,
        "equity": max(equity_t, 0.0),
        "equity_raw": equity_t,
    }

    # ---------------- Returns ----------------
    if wiped:
        irr, moic = -1.0, 0.0  # total loss of the equity cheque
    else:
        cashflows = [-equity0] + [0.0] * (n - 1) + [equity_t]
        irr = _irr(cashflows)
        moic = equity_t / equity0

    # ---------------- Value-creation bridge ----------------
    # profit = E_T - E_0
    #        = [m0*(EBITDA_T - EBITDA_0)]        <- EBITDA growth at the entry multiple
    #        + [(m_T - m0)*EBITDA_T]             <- multiple expansion on exit earnings
    #        + [ND_0 - ND_T]                     <- debt paydown / cash build (deleveraging)
    #        - fees                              <- transaction costs
    # This ties EXACTLY: E_T = m_T*EBITDA_T - ND_T and E_0 = m0*EBITDA_0 + fees - ND_0.
    attribution = {
        "ebitda_growth": m_entry * (ebitda_t - company.ebitda),
        "multiple_expansion": (m_exit - m_entry) * ebitda_t,
        "debt_paydown": debt0 - net_debt_t,
        "fees": -fees,
        "total_profit": equity_t - equity0,
    }

    return LBOResult(
        irr=irr,
        moic=moic,
        feasible=True,
        failure_reason=None,
        equity_wiped=wiped,
        yearly=yearly,
        entry=entry,
        exit=exit_,
        attribution=attribution,
        case_label=case.label,
    )


def _row(
    year: int,
    ebitda: float,
    revenue: float,
    capex: float,
    delta_wc: float,
    taxes: float,
    cfads: float,
    cash_open: float,
    interest: dict[str, float],
    amort: dict[str, float],
    sweep: dict[str, float],
    closing: dict[str, float],
    cash_close: float,
    a: MarketAssumptions,
    rcf_interest: float = 0.0,
    rcf_draw: float = 0.0,
    rcf_repay: float = 0.0,
    rcf_balance: float = 0.0,
    coverage: float | None = None,
    leverage: float | None = None,
    covenant_cap: float | None = None,
    breach: str | None = None,
) -> dict:
    """Assemble one row of the yearly waterfall table (flat, UI-ready)."""
    total_interest = sum(interest.values()) + rcf_interest
    total_debt_close = sum(closing.values()) + rcf_balance
    row: dict = {
        "year": year,
        "ebitda": ebitda,
        "revenue": revenue,
        "capex": capex,
        "delta_wc": delta_wc,
        "taxes": taxes,
        "cfads": cfads,
        "cash_opening": cash_open,
        "interest_total": total_interest,
        "amort_total": sum(amort.values()),
        "sweep_total": sum(sweep.values()),
        "rcf_interest": rcf_interest,
        "rcf_draw": rcf_draw,
        "rcf_repay": rcf_repay,
        "rcf_balance": rcf_balance,
        "cash_closing": cash_close,
        "total_debt_closing": total_debt_close,
        "net_debt_closing": total_debt_close - cash_close,
        "coverage_ratio": coverage if coverage is not None else np.nan,
        "coverage_covenant": a.min_interest_coverage,
        "leverage_ratio": leverage if leverage is not None else np.nan,
        "leverage_covenant": covenant_cap if covenant_cap is not None else np.nan,
        "covenant_breach": breach or "",
    }
    for name in interest:
        row[f"interest_{name}"] = interest[name]
        row[f"amort_{name}"] = amort[name]
        row[f"sweep_{name}"] = sweep[name]
        row[f"balance_{name}"] = closing[name]
    return row


def run_stress(
    company: CompanyData,
    a: MarketAssumptions,
    stack: list[SizedTranche],
) -> tuple[LBOResult, bool]:
    """Run the downside case. A structure SURVIVES iff the stressed run stays
    feasible (no covenant breach, no cash shortfall) AND the equity is not
    wiped at the stressed exit. Survival — not stressed IRR — is the bar,
    matching how an IC underwrites downside: the question is whether the
    equity survives with the company intact, not what it earns in a recession."""
    result = run_lbo(company, a, stack, stress_case(company, a))
    survives = result.feasible and not result.equity_wiped
    return result, survives
