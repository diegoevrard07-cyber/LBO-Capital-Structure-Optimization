"""Debt tranches and the capital-stack builder.

The stack is the decision variable of the whole tool: how many turns of
EBITDA to raise, and how to split that raise across senior and junior paper.

Pricing reality the model must capture: debt is not one price. Cheap senior
capacity is capped (lenders underwrite to a leverage ceiling), so each
incremental turn of leverage beyond that cap must be placed in progressively
more expensive junior tranches. The blended cost of debt is therefore CONVEX
in total leverage — and that convexity is one of the three forces that make
the optimal capital structure interior rather than "maximum leverage".

``senior_mix`` scales the senior tranche's usable cap: at mix = 1.0 the full
senior cap is available and the structure is as cheap as the market allows;
at lower mixes the sponsor deliberately (or forcibly, at the tight end of the
credit cycle) places less senior paper and spills the remainder into junior
tranches. Total leverage x senior mix is thus a genuine 2-D decision space.
"""

from __future__ import annotations

from dataclasses import dataclass

from .inputs import MarketAssumptions


@dataclass(frozen=True)
class DebtTranche:
    """A financing layer as offered by the market, before sizing.

    ``margin_or_rate`` is a spread over the base rate for floating tranches
    and an all-in fixed coupon for fixed tranches. ``cap_turns`` is the
    maximum size of this layer in turns of EBITDA. ``amort_pct`` is the
    mandatory annual amortization as a fraction of the ORIGINAL face.
    ``seniority`` orders the waterfall: 1 is paid first, swept first, and is
    the last to absorb losses.
    """

    name: str
    margin_or_rate: float
    is_floating: bool
    cap_turns: float
    amort_pct: float
    seniority: int


@dataclass
class SizedTranche:
    """A market tranche after sizing against a specific target's EBITDA."""

    tranche: DebtTranche
    amount: float  # face value drawn, in currency millions
    turns: float = 0.0  # size in turns of EBITDA, set by the stack builder

    def cash_rate(self, base_rate: float) -> float:
        """All-in annual cash-pay rate at a given base rate."""
        if self.tranche.is_floating:
            return base_rate + self.tranche.margin_or_rate
        return self.tranche.margin_or_rate


def market_tranches(a: MarketAssumptions) -> list[DebtTranche]:
    """The three financing layers, cheapest first, from the assumption set."""
    return [
        DebtTranche(
            name="Senior TLB",
            margin_or_rate=a.senior_margin,
            is_floating=True,
            cap_turns=a.senior_cap,
            amort_pct=a.senior_amort_pct,
            seniority=1,
        ),
        DebtTranche(
            name="Second Lien",
            margin_or_rate=a.second_lien_margin,
            is_floating=True,
            cap_turns=a.second_lien_cap,
            amort_pct=a.second_lien_amort_pct,
            seniority=2,
        ),
        DebtTranche(
            name="Mezzanine",
            margin_or_rate=a.mezz_rate,
            is_floating=False,
            cap_turns=a.mezz_cap,
            amort_pct=a.mezz_amort_pct,
            seniority=3,
        ),
    ]


def build_stack(
    target_leverage_turns: float,
    senior_mix: float,
    ebitda: float,
    assumptions: MarketAssumptions,
) -> list[SizedTranche]:
    """Size a stack for ``target_leverage_turns`` of EBITDA, cheapest-first.

    ``senior_mix`` in [0, 1] scales the senior tranche's usable cap before
    the raise spills into junior paper. Filling cheapest-first is not a
    modelling convenience — it is what any arranger actually does, because no
    rational borrower pays junior coupons while senior capacity remains.

    Raises ``ValueError`` if the target exceeds total capacity at this mix;
    the optimizer treats that as an inadmissible grid point, not a crash.
    """
    if not -1e-9 <= senior_mix <= 1.0 + 1e-9:
        raise ValueError(f"senior_mix must lie in [0, 1], got {senior_mix}")
    senior_mix = min(max(senior_mix, 0.0), 1.0)  # absorb float dust from grid arange
    if target_leverage_turns < 0:
        raise ValueError(f"target leverage must be non-negative, got {target_leverage_turns}")

    remaining = target_leverage_turns
    stack: list[SizedTranche] = []
    for tranche in market_tranches(assumptions):
        usable_cap = tranche.cap_turns
        if tranche.seniority == 1:
            usable_cap *= senior_mix
        take = min(remaining, usable_cap)
        if take > 1e-12:
            stack.append(SizedTranche(tranche=tranche, amount=take * ebitda, turns=take))
            remaining -= take
        if remaining <= 1e-12:
            break

    if remaining > 1e-9:
        capacity = sum(t.cap_turns for t in market_tranches(assumptions)[1:])
        capacity += assumptions.senior_cap * senior_mix
        raise ValueError(
            f"Target of {target_leverage_turns:.2f}x exceeds total capacity of "
            f"{capacity:.2f}x at senior mix {senior_mix:.0%}."
        )
    return stack


def total_debt(stack: list[SizedTranche]) -> float:
    """Total face value drawn across the stack."""
    return sum(t.amount for t in stack)


def blended_rate(stack: list[SizedTranche], base_rate: float) -> float:
    """Weighted-average cash cost of the stack at a given base rate.

    The convexity story in one function: holding base rates fixed, this rises
    non-linearly in leverage because each marginal turn is placed in a more
    expensive tranche once the cheaper caps are exhausted.
    """
    debt = total_debt(stack)
    if debt <= 0:
        return 0.0
    return sum(t.amount * t.cash_rate(base_rate) for t in stack) / debt


if __name__ == "__main__":
    # Demo: blended cash cost of debt from 3.0x to 6.5x at full senior mix.
    # Watch the kink at 4.0x — the senior cap — where marginal turns start
    # pricing at second-lien and then mezzanine coupons. That kink is the
    # convexity that helps create an interior optimum.
    a = MarketAssumptions()
    print(f"Blended cost of debt by leverage (base rate {a.base_rate:.2%}, senior mix 100%)")
    print(f"{'Leverage':>10} {'Blended rate':>14} {'Marginal tranche':>18}")
    lev = 3.0
    while lev <= 6.5 + 1e-9:
        stack = build_stack(lev, 1.0, 100.0, a)
        marginal = stack[-1].tranche.name
        print(f"{lev:>9.2f}x {blended_rate(stack, a.base_rate):>13.2%} {marginal:>18}")
        lev += 0.5
