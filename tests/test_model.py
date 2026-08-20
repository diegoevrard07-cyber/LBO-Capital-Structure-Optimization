"""Engine tests, written alongside the model rather than after it.

Each test encodes a piece of FINANCE the engine must respect — conservation
of debt balances, the leverage amplifier, covenant discipline, waterfall
seniority — not just a code path. A synthetic company is used throughout so
the suite runs offline and deterministically.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.debt import build_stack, blended_rate, total_debt
from src.inputs import CompanyData, MarketAssumptions
from src.model import base_case, run_lbo, run_stress
from src.risk import monte_carlo


def make_company(**overrides) -> CompanyData:
    """A clean, LBO-able mid-cap: 8x entry, 3% growth, 25% tax, 4% capex."""
    defaults = dict(
        ticker="TEST.L",
        name="TestCo plc",
        currency="GBP",
        ebitda=100.0,
        revenue=1000.0,
        ev_ebitda=8.0,
        capex_pct=0.04,
        tax_rate=0.25,
        growth=0.03,
        warnings=[],
    )
    return CompanyData(**(defaults | overrides))


A = MarketAssumptions()


# ---------------------------------------------------------------------------
# 1. Zero-debt sanity: with no leverage the deal is just "buy and hold the
#    asset", and the engine must reproduce the hand-computed answer exactly.
# ---------------------------------------------------------------------------
def test_zero_debt_matches_analytic_solution():
    company = make_company()
    stack = build_stack(0.0, 1.0, company.ebitda, A)
    assert stack == []
    res = run_lbo(company, A, stack, base_case(company))

    assert res.feasible and not res.equity_wiped
    m = company.ev_ebitda + A.entry_premium_turns  # 9.0x
    ev0 = m * company.ebitda
    fees = A.ev_fee_pct * ev0  # no debt -> no debt fees
    equity0 = ev0 + fees

    # Independent hand calculation of the all-equity deal: FCF accumulates as
    # cash (nothing to sweep against), exit equity = exit EV + cash pile.
    cash = 0.0
    for year in range(1, A.hold_years + 1):
        ebitda = company.ebitda * (1 + company.growth) ** year
        revenue = company.revenue * (1 + company.growth) ** year
        revenue_prev = company.revenue * (1 + company.growth) ** (year - 1)
        capex = company.capex_pct * revenue
        taxes = (ebitda - capex) * company.tax_rate  # D&A = capex, no interest
        cash += ebitda - capex - taxes - A.wc_pct_of_delta_rev * (revenue - revenue_prev)
    ebitda_t = company.ebitda * (1 + company.growth) ** A.hold_years
    expected_equity_t = m * ebitda_t + cash

    assert res.exit["equity"] == pytest.approx(expected_equity_t, rel=1e-9)
    assert res.moic == pytest.approx(expected_equity_t / equity0, rel=1e-9)
    # The headline approximation an IC would sanity-check against — MOIC is
    # modestly ABOVE the pure EV ratio because unswept cash accumulates:
    assert res.moic == pytest.approx((m * ebitda_t) / equity0, rel=0.30)
    assert res.moic > (m * ebitda_t) / equity0


# ---------------------------------------------------------------------------
# 2. Balance conservation: opening - amort - sweep = closing, per tranche,
#    per year. This is the waterfall's accounting identity.
# ---------------------------------------------------------------------------
def test_balance_conservation_per_tranche_per_year():
    # An asset-lighter company at 5.0x stays feasible for the full hold, so
    # the identity is tested over all five years and every tranche.
    company = make_company(capex_pct=0.02)
    stack = build_stack(5.0, 1.0, company.ebitda, A)
    res = run_lbo(company, A, stack, base_case(company))
    assert res.feasible, res.failure_reason

    prev_closing = {t.tranche.name: t.amount for t in stack}
    for _, row in res.yearly.iterrows():
        for t in stack:
            name = t.tranche.name
            opening = prev_closing[name]
            closing = row[f"balance_{name}"]
            assert opening - row[f"amort_{name}"] - row[f"sweep_{name}"] == pytest.approx(
                closing, abs=1e-9
            )
            prev_closing[name] = closing


# ---------------------------------------------------------------------------
# 3. Leverage amplifier: with covenants disabled and generous cash flows,
#    IRR must rise monotonically with leverage — the mechanical force the
#    constraints exist to discipline.
# ---------------------------------------------------------------------------
def test_irr_monotonically_increasing_in_leverage_without_covenants():
    company = make_company(ebitda=200.0, growth=0.05, capex_pct=0.02)
    irrs = []
    for leverage in np.arange(2.0, 6.51, 0.5):
        stack = build_stack(float(leverage), 1.0, company.ebitda, A)
        res = run_lbo(company, A, stack, base_case(company), check_covenants=False)
        assert res.feasible, res.failure_reason
        irrs.append(res.irr)
    assert all(b > a for a, b in zip(irrs, irrs[1:])), f"IRR not monotone in leverage: {irrs}"


# ---------------------------------------------------------------------------
# 4. Covenant triggers: constructed cases must fail with the SPECIFIC reason.
# ---------------------------------------------------------------------------
def test_coverage_covenant_breach_is_flagged_with_reason():
    # At 6.75x the year-1 interest bill is ~53.4 against EBITDA of 103 —
    # coverage of 1.93x breaches the 2.0x minimum. Low capex keeps CFADS
    # above debt service, isolating the covenant (not a cash shortfall).
    company = make_company(capex_pct=0.02)
    stack = build_stack(6.75, 1.0, company.ebitda, A)
    res = run_lbo(company, A, stack, base_case(company))
    assert not res.feasible
    assert "interest coverage covenant breach in year 1" in res.failure_reason
    assert res.irr is None and res.moic is None


def test_leverage_covenant_breach_is_flagged_with_reason():
    # A shrinking business with no sweep: net leverage drifts UP while the
    # covenant steps DOWN to its 4.0x floor — a leverage breach in year 3,
    # with coverage comfortable throughout (so the reason string is unambiguous).
    company = make_company(growth=-0.02)
    a = MarketAssumptions(cash_sweep_pct=0.0)
    stack = build_stack(4.5, 1.0, company.ebitda, a)
    res = run_lbo(company, a, stack, base_case(company))
    assert not res.feasible
    assert "net leverage covenant breach in year 3" in res.failure_reason


# ---------------------------------------------------------------------------
# 5. IRR/MOIC consistency: with no interim distributions the two return
#    measures are the same number seen from different angles.
# ---------------------------------------------------------------------------
def test_irr_moic_consistency_no_recap():
    company = make_company()
    stack = build_stack(4.5, 1.0, company.ebitda, A)
    res = run_lbo(company, A, stack, base_case(company))
    assert res.feasible and not res.equity_wiped
    assert res.irr == pytest.approx(res.moic ** (1 / A.hold_years) - 1, rel=1e-6)


# ---------------------------------------------------------------------------
# 6. Waterfall priority: junior tranches receive no sweep while any senior
#    balance remains, and bullet tranches never amortize.
# ---------------------------------------------------------------------------
def test_sweep_respects_seniority():
    company = make_company()
    stack = build_stack(6.0, 1.0, company.ebitda, A)  # senior + second lien + mezz
    assert [t.tranche.name for t in stack] == ["Senior TLB", "Second Lien", "Mezzanine"]
    res = run_lbo(company, A, stack, base_case(company), check_covenants=False)
    assert res.feasible

    for _, row in res.yearly.iterrows():
        junior_sweep = row["sweep_Second Lien"] + row["sweep_Mezzanine"]
        if junior_sweep > 1e-9:
            assert row["balance_Senior TLB"] == pytest.approx(0.0, abs=1e-9)
        # Bullets never amortize; only the senior TLB carries 1% amortization.
        assert row["amort_Second Lien"] == 0.0
        assert row["amort_Mezzanine"] == 0.0


# ---------------------------------------------------------------------------
# 7. Frontier monotonicity: with common random numbers (same seed), distress
#    probability must be non-decreasing in leverage — the economic content of
#    the efficient-frontier chart.
# ---------------------------------------------------------------------------
def test_distress_probability_non_decreasing_in_leverage():
    company = make_company()
    p_distress = []
    for leverage in [3.0, 4.0, 5.0, 6.0]:
        stack = build_stack(leverage, 1.0, company.ebitda, A)
        mc = monte_carlo(company, A, stack, n=300, seed=42)
        p_distress.append(mc.p_distress)
    assert all(
        b >= a - 1e-12 for a, b in zip(p_distress, p_distress[1:])
    ), f"P(distress) not monotone in leverage: {p_distress}"
    assert p_distress[0] == 0.0  # 3x leverage on this credit should never distress
    assert p_distress[-1] > 0.0  # 6x leverage genuinely risks the equity


# ---------------------------------------------------------------------------
# Stack-builder mechanics and the stress overlay.
# ---------------------------------------------------------------------------
def test_stack_fills_cheapest_first_and_caps_capacity():
    stack = build_stack(5.0, 1.0, 100.0, A)
    assert [t.tranche.name for t in stack] == ["Senior TLB", "Second Lien"]
    assert total_debt(stack) == pytest.approx(500.0)
    # Senior mix scales senior capacity: at 50% mix only 2.0x of senior is
    # usable, so a 5.0x raise spills further into junior paper.
    stack_low_mix = build_stack(5.0, 0.5, 100.0, A)
    assert stack_low_mix[0].turns == pytest.approx(2.0)
    with pytest.raises(ValueError, match="exceeds total capacity"):
        build_stack(6.0, 0.5, 100.0, A)  # capacity at 50% mix is 5.0x


def test_blended_rate_is_convex_in_leverage():
    # Convexity means the MARGINAL cost of each additional turn is
    # non-decreasing and jumps at tranche-cap boundaries: 7.25% inside the
    # senior cap, then 10.25% (second lien), then a blended junior increment.
    levels = (3.0, 4.0, 5.0, 6.0)
    interest_bills = [
        blended_rate(build_stack(lev, 1.0, 100.0, A), A.base_rate) * lev for lev in levels
    ]
    marginal_costs = np.diff(interest_bills)  # cost of each extra 1.0x turn
    assert marginal_costs[0] == pytest.approx(0.0725)  # pure senior pricing
    assert marginal_costs[1] == pytest.approx(0.1025)  # a full turn of second lien
    assert all(b > a for a, b in zip(marginal_costs, marginal_costs[1:]))


def test_stress_survival_and_wipeout_flags():
    company = make_company()
    safe = build_stack(3.5, 1.0, company.ebitda, A)
    _, survives_safe = run_stress(company, A, safe)
    assert survives_safe

    aggressive = build_stack(6.5, 1.0, company.ebitda, A)
    stress_res, survives_aggressive = run_stress(company, A, aggressive)
    assert not survives_aggressive
    assert (not stress_res.feasible) or stress_res.equity_wiped
