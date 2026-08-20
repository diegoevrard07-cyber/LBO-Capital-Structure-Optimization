# StackOptimal — Walkthrough

Every mechanism in the engine, explained the way you would defend it in a
private-equity interview: what it does, why it is built that way, and what the
commercial meaning of each number is.

---

## 1. The core concept

An LBO's IRR rises mechanically with leverage: the same exit profit is divided
across a smaller equity cheque. If that were the whole story, every deal would
run at maximum debt capacity. Three forces create an **interior optimum**:

1. **The marginal cost of debt rises non-linearly.** Cheap senior capacity is
   capped (lenders underwrite to a leverage ceiling), so incremental turns of
   leverage must be placed in second-lien and mezzanine paper at materially
   higher coupons. The blended cost of debt is convex in leverage.
2. **Covenants and cash adequacy bind.** Interest coverage and a stepped
   net-leverage covenant must pass in *every* year, and there is no revolver
   to paper over a bad year.
3. **Downside fragility explodes.** A thin equity cushion is wiped out by a
   mild recession; the stressed downside — not the base case — is what kills
   highly-levered structures.

The optimizer therefore maximizes base-case IRR **subject to** surviving a
stressed downside and satisfying every covenant in every year. The headline
output is the gap between the naive max-IRR structure and the survivable
optimum — how much IRR you give up to own a structure that survives.

---

## 2. Sources & uses mechanics

At close, the **uses** of funds are the purchase price (enterprise value) plus
transaction and financing fees; the **sources** are the debt stack plus the
sponsor equity cheque. The equity cheque is the *plug*:

```
EV₀      = entry multiple × entry EBITDA
fees     = 2.5% × debt raised  +  1.5% × EV        (capitalized into uses)
equity₀  = EV₀ + fees − total debt
```

Two consequences worth being able to say out loud:

- **Fees punish leverage at the margin.** Debt fees are 2.5% of debt *raised*,
  so each incremental turn of leverage adds 2.5% of a turn to the equity
  cheque — leverage is not free even before interest.
- **Negative equity is a real constraint.** If debt capacity ever exceeded
  uses, the model rejects the structure (`negative equity at entry`) rather
  than returning a nonsense negative cheque. This binds for low-multiple
  targets at high leverage.

The entry multiple is the market EV/EBITDA **plus a 1.0x control premium** —
you do not win an auction at the screen price.

---

## 3. Why the blended cost of debt is convex in leverage

The stack fills **cheapest-first**, which is not a modelling convenience — it
is what any arranger does, because no rational borrower pays junior coupons
while senior capacity remains:

| Layer | Pricing | Cap | Amortization |
|---|---|---|---|
| Senior TLB | SONIA + 350bp | 4.0x EBITDA | 1%/yr, bullet remainder |
| Second lien | SONIA + 650bp | +1.5x | Bullet |
| Mezzanine | 12.0% fixed cash-pay | +1.5x | Bullet |

Because each cap is finite, the *marginal* turn of leverage gets more
expensive in steps: turns up to 4.0x price at ~7.25%, the next 1.5x at
~10.25%, the next at 12%. The **blended** rate is therefore a convex,
piecewise-kinked function of total leverage — run `python -m src.debt` to see
the table. This convexity is the first force disciplining leverage: past the
senior cap, each additional turn buys less IRR per unit of risk.

The **senior mix** parameter scales the senior tranche's *usable* cap. At 100%
mix the full 4.0x of senior is available; at lower mixes the raise spills
earlier into junior paper. This makes the decision genuinely two-dimensional
(total leverage × mix), though the optimum almost always sits at 100% senior —
senior debt is strictly cheaper, so a lower mix only ever worsens coverage.
The tool flags that edge honestly rather than pretending the mix dimension is
interesting when it is not.

---

## 4. The waterfall: path dependence forces simulation

Each projection year runs the same strict sequence:

```
CFADS  = EBITDA − capex − cash taxes − Δ working capital
  1. cash interest on OPENING balances (all tranches + drawn revolver
     + commitment fee on undrawn revolver capacity)
  2. mandatory amortization (senior: 1% of original face)
  3. excess cash flow  =  CFADS − interest − amortization
     if NEGATIVE: covered from cash on hand, then a revolver draw;
                  a payment default only once both are exhausted
     if POSITIVE: repay any drawn revolver FIRST and in full, then
  4. cash sweep: 75% of the remainder prepays term debt senior-first
  5. the retained 25% accumulates as balance-sheet cash
```

**Why not a closed form?** Because this year's paydown changes next year's
opening balances, which change next year's interest, which changes next year's
*taxes* (interest is deductible), which changes next year's free cash flow
available to sweep. The system is a recurrence, not a formula. Add covenant
tests that kink the feasible region and tranche caps that kink the cost of
funds, and no analytic solution survives. So the engine simulates year by
year — which is also exactly how a deal team builds it in Excel, and therefore
auditable line by line. The balance identity (opening − amort − sweep =
closing, per tranche, per year) is enforced by construction and verified by
test, and the whole schedule is reconciled against a fully hand-computed
example in [VALIDATION.md](VALIDATION.md).

Mechanical details that matter:

- **Interest on opening balances — and why there is no circularity.** In a
  model that charges interest on the AVERAGE balance, interest depends on the
  closing balance, which depends on the sweep, which depends on cash flow,
  which depends on interest — a circular reference that Excel modellers break
  with iteration or a copy-paste macro. The beginning-of-period convention
  cuts the loop cleanly: this year's interest is fully determined by last
  year's closing balances. It is a standard, defensible convention (and
  mildly conservative — the year's paydown earns no intra-year interest
  relief), applied identically to every tranche and the revolver. Stating the
  convention is the point; hiding it would be the error.
- **The sweep applies to the YEAR's excess cash flow, not the cash balance.**
  Credit agreements define the ECF sweep on the year's excess cash flow; a
  model that sweeps the whole cash balance quietly re-sweeps last year's
  retained 25% and overstates deleveraging while claiming 25% retention. Here
  retained cash genuinely stays retained (it reduces net debt and funds bad
  years); the stated sweep percentage means what it says.
- **The revolver is a backstop, not a source.** Undrawn at close, it draws
  only when a year's cash flow cannot cover debt service (after cash on hand
  is used), pays margin on drawn amounts plus a commitment fee on undrawn
  capacity, and is repaid first — before any term-loan sweep — as its
  super-senior ranking requires. A shortfall beyond cash plus the remaining
  commitment is a payment default.
- **Taxes on (EBIT − interest − financing-fee amortization), floored at
  zero.** The interest tax shield is real and is one of leverage's genuine
  economic benefits. Capitalized financing fees amortize straight-line over
  the hold as a non-cash deduction (the deferred-financing-fee treatment) —
  the cash left at close inside the equity cheque; only the tax shield
  arrives over time. The floor means we never book a tax *refund* in a loss
  year (no carry-forward modelling — see simplifications).
- **D&A = capex.** Steady-state assumption: the asset base is held constant,
  so depreciation equals reinvestment. It keeps EBIT honest without modelling
  an asset register.
- **The sweep prepays senior-first and cannot overpay.** Once every tranche is
  repaid, the unspent sweep pool stays as cash — the engine never leaks cash
  into thin air (a real bug class in quick-and-dirty LBO models, caught here
  by the zero-debt analytic test).

---

## 5. Covenants: how each one binds and what breach means commercially

Two covenants are tested at the end of every projection year:

**Interest coverage: EBITDA / cash interest ≥ 2.0x.** This binds *early* —
year 1 is almost always the tightest point, before deleveraging and EBITDA
growth build headroom. It is the binding constraint on *very* aggressive
structures: at ~6x leverage and a ~8.4% blended cost, year-1 interest is over
half of EBITDA and coverage dips under 2.0x. Commercially, a coverage breach
is an event of default: lenders can accelerate, and in practice it triggers a
restructuring negotiation in which the sponsor loses control of the timetable
— and usually the equity.

**Net leverage: (debt − cash) / EBITDA ≤ entry leverage + 0.5x, stepping down
0.5x per year to a 4.0x floor.** This binds on *slow deleveragers*: a
structure can pass coverage comfortably and still breach leverage in years 3–5
if the sweep is weak or EBITDA stalls, because the covenant walks down while
net debt does not. The 4.0x floor encodes a market reality: lenders will not
write a covenant tighter than where they would underwrite a new deal.

A third failure mode is not a covenant at all: a **cash shortfall** is a
payment default. The model gives a structure the same two lifelines a real
deal has — cash on the balance sheet, then a revolver draw up to the
committed 0.5x of EBITDA — and fails it only when both are exhausted. That is
the correct severity ordering: liquidity stress is survivable up to the
commitment; insolvency of the cash flow itself is not.

Every failure returns a specific sentence (`interest coverage covenant breach
in year 1: 1.93x < 2.00x minimum`), never a bare NaN, because the UI and the
memo must say *why* a structure dies.

---

## 6. The value-creation bridge

Equity profit decomposes exactly into three drivers plus fees:

```
profit = E_T − E₀
       = m₀ × (EBITDA_T − EBITDA₀)     EBITDA growth at the entry multiple
       + (m_T − m₀) × EBITDA_T          multiple expansion on exit earnings
       + (ND₀ − ND_T)                   debt paydown & cash build
       − fees                           transaction costs
```

This ties *exactly* — substitute `E_T = m_T·EBITDA_T − ND_T` and
`E₀ = m₀·EBITDA₀ + fees − ND₀` and the identity falls out. The default case
underwrites **zero multiple expansion** (exit at entry multiple), so every
penny of profit comes from growth and deleveraging. That is the intellectually
honest way to present an LBO: multiple arbitrage is a hope, not a plan, and
the attribution chart makes it impossible to hide when a deal's returns are
really a multiple bet.

---

## 7. Why grid search, not gradient methods

The debt waterfall makes the objective non-convex and path-dependent —
covenant tests kink the feasible region, tranche-cap boundaries kink the cost
of funds, and the cash sweep switches the mapping from this year's free cash
flow to next year's interest bill. Gradient methods on such a surface silently
converge to local optima and say nothing about the surrounding terrain. A
transparent grid scan finds the global optimum over the discretized space,
costs milliseconds per evaluation, and yields the full risk-return surface as
a free by-product.

There is a second, practical reason: the grid *is* the deliverable. An IC does
not want a point estimate; it wants to see the whole leverage × mix surface —
which cells breach, which survive, and how much IRR is being left on the table
at the constraint boundary. The 231-point scan with base and stress runs
completes in well under a second.

---

## 8. What the frontier chart demonstrates

The centerpiece chart plots **median IRR against P(distress)** — the
probability of covenant breach or equity wipeout from the Monte Carlo — across
leverage levels at the optimal mix. It is the capital-structure efficient
frontier, and it reframes private equity as what it actually is: **constrained
risk-return optimization**, not IRR maximization.

Three things to say about it in an interview:

1. **Return rises steadily with leverage; survival does not.** Median IRR
   climbs while distress probability stays near zero — then distress rises
   sharply once coverage and the leverage covenant start binding across the
   draw distribution. The practical conclusion: stop before distress turns up.
2. **The naive max-IRR point sits past that edge.** It looks better on every
   headline metric and loses the company in a recession. The gap between it
   and the starred optimum — a few tenths of a point of IRR — is the price of
   survivability, and it is the single most important number in the tool.
3. **The frontier is conditional on survival.** IRR statistics are computed
   over draws that reach exit (wiped equity contributes −100%); distressed
   draws are reported through P(distress), not silently averaged in.
   Survivorship bias would otherwise flatter every leveraged structure.

---

## 9. Every default assumption and its justification

Financing (European leveraged-finance market, mid-2026):

| Assumption | Default | Justification |
|---|---|---|
| Base rate | 3.75% | SONIA proxy; Bank of England Bank Rate path after the 2024–25 easing cycle |
| Senior TLB margin | S+350bp | Large-cap European institutional term-loan clearing level |
| Second-lien margin | S+650bp | ~300bp over senior is standard second-lien economics |
| Mezzanine coupon | 12.0% fixed | Cash-pay mezzanine; PIK toggle left as a documented extension |
| Senior cap | 4.0x | Post-2022 European norm for solid credits |
| Second-lien / mezz caps | +1.5x each | Total market capacity 7.0x |
| Senior amortization | 1%/yr | TLB market standard; remainder bullet |
| Revolver | 0.5x committed, S+300, 50bp undrawn fee | Liquidity backstop; revolvers price inside the TLB; undrawn at close |
| Debt fees | 2.5% of debt | Blended OID/arrangement; amortized straight-line over the hold as a tax deduction |
| Transaction fees | 1.5% of EV | Advisory, legal, diligence — mid-market all-in |

Covenants and structure:

| Assumption | Default | Justification |
|---|---|---|
| Min interest coverage | 2.0x | Standard European covenant test |
| Leverage covenant | entry +0.5x, −0.5x/yr, floor 4.0x | Opening headroom plus expected deleveraging; lenders won't covenant below market |
| Hold | 5 years | Classic PE hold period |
| Exit multiple | = entry multiple | Conservative: no expansion underwritten |
| Entry premium | +1.0x over market | Control premium to win the asset |
| Cash sweep | 75% of each year's excess cash flow | Market-standard ECF sweep; the retained 25% genuinely accumulates |
| Working capital | 2% of incremental revenue | Asset-light norm |
| Stress case | −20% growth, −10% year-1 EBITDA, −1.0x exit | A recession year that permanently resets the earnings base |
| MC sigmas | growth 3pp, multiple 0.75x, rate 1pp | One-standard-deviation misses calibrated to cycle history |
| MC draws / seed | 5,000 / 42 | P5/P95 stable to ~0.1pp; fixed seed for reproducibility |

---

## 10. Known simplifications, and how each would be extended

- **Annual granularity.** The revolver bridges YEAR-level shortfalls; a real
  RCF also absorbs intra-year working-capital swings the model cannot see.
  *Extension: quarterly periods, which mostly matters for seasonal
  businesses.*
- **No interest income on cash.** Retained cash reduces net debt but earns
  nothing — mildly conservative, and it avoids pretending a deposit rate is a
  forecast. *Extension: credit cash at base rate less a spread.*
- **No PIK interest.** Mezzanine is modelled cash-pay at 12%. *Extension: add
  a `pik_fraction` to `DebtTranche`; accrue the PIK portion to the balance and
  exclude it from cash interest (and from the coverage numerator's cash
  interest, per most covenant definitions).*
- **D&A = capex.** Steady-state asset base. *Extension: model a capex/D&A
  schedule with a depreciation lag, which matters for businesses with lumpy
  capex cycles.*
- **No dividend recaps.** The cash-flow vector is `[−E₀, 0, …, E_T]`; the IRR
  solver is already recap-ready (it handles multiple sign changes via
  polynomial roots). *Extension: add an optional year-3 recap that re-levers
  to a target ratio and drops the proceeds into the cash-flow vector.*
- **Single currency.** Everything is in the target's reporting currency.
  *Extension: FX-hedge the tranches for cross-currency structures, with hedge
  cost in the blended rate.*
- **No loss carry-forwards.** Taxes are floored at zero in loss years rather
  than generating a usable deferred tax asset. *Extension: track a tax-loss
  balance and offset it against future taxable income — mildly flattering to
  aggressive structures in the recovery years.*
- **Public-filings data only.** yfinance fundamentals are noisier than paid
  loan-market data (PitchBook LCD was deliberately not used). *Extension:
  allow manual override of every fetched field in the UI; the assumptions
  panel already works this way for market parameters.*

---

## 11. The ten hardest questions a skeptical banker will ask

**1. "Do you charge interest on beginning, average, or ending balances — and
where's your circularity handling?"**
Beginning-of-period, on every tranche and the revolver. That convention makes
this year's interest fully determined by last year's closing balances, so
there is no interest↔sweep circularity to iterate away — the loop is cut by
convention, not solved numerically. It is mildly conservative: the year's
paydown earns no intra-year interest relief. If you want average-balance
interest, the engine would need a one-dimensional fixed-point iteration per
year; it converges in a few passes because interest is a small share of cash
flow, but it buys precision the annual granularity doesn't support.

**2. "Why didn't the optimizer just pick maximum leverage?"**
Three reasons, in the order they bind. Above 7.0x there is no market: the
tranche caps (4.0x senior + 1.5x second lien + 1.5x mezz) are a hard
capacity constraint. From ~6x the base case itself fails — year-1 EBITDA/cash
interest dips under the 2.0x covenant because each marginal turn prices at
second-lien and mezzanine coupons. And everything above the recommended
structure fails the stressed downside — with a −10% recession year and a
−1.0x exit, the net-leverage covenant (stepping down 0.5x a year) breaches
around year 3. The recommended structure is the highest leverage that
survives all three. The heatmap shows the whole story: the bright cells end
well before the capacity edge.

**3. "Your sweep is 75% — of what, exactly?"**
Of each year's excess cash flow: CFADS minus cash interest minus mandatory
amortization, after repaying any drawn revolver in full. Not of the cash
balance — that would re-sweep the previously retained 25% and quietly
overstate deleveraging. The retained share accumulates, reduces net debt at
exit, and funds bad years before the revolver draws.

**4. "How does your revolver actually work?"**
0.5x of EBITDA committed, undrawn at close, S+300 on drawn balances and a
50bp commitment fee on undrawn capacity (both tax-deductible, both in the
coverage denominator). It draws only when a year's cash flow cannot cover
debt service after cash on hand is used, and it is repaid first — before a
penny of term-loan sweep — because it ranks super-senior. A shortfall beyond
cash plus the remaining commitment is a payment default and kills the
structure with a specific reason string.

**5. "Is your IRR a real IRR or MOIC dressed up?"**
A real one: the engine builds the full cash-flow vector — equity cheque out
at close, zero interim flows (no dividends or recaps underwritten), exit
equity in year 5 — and solves NPV = 0 via the polynomial roots of the
discount-factor equation. With that two-point vector the answer coincides
with MOIC^(1/5)−1, which the test suite uses as a cross-check, but the solver
does not assume it; add a recap flow and it still works (taking the root
nearest a plausible hurdle when multiple sign changes create several).

**6. "What creates the tax shield, and did you floor it?"**
Taxes are 25% of (EBIT − cash interest − financing-fee amortization), floored
at zero. Interest deductibility is leverage's genuine economic benefit and is
fully captured; the 2.5% financing fees amortize straight-line over the hold
as a non-cash deduction, the deferred-financing-fee treatment. The floor
means loss years generate no refund — I don't model carry-forwards, which is
conservative for aggressive structures in recovery years.

**7. "Your exit equals your entry multiple. Isn't that hiding multiple
expansion in the entry premium?"**
The entry is market EV/EBITDA plus a 1.0x control premium — you pay it to
win the auction — and the exit is that same all-in multiple. So the bridge
attributes exactly zero profit to multiple expansion; every penny is EBITDA
growth and deleveraging, and the attribution identity ties to the penny
(E_T − E_0 decomposes exactly; it's a two-line algebra proof). If anything
the assumption is harsh: it assumes you exit at your full bid, premium
included, with no re-rating either way.

**8. "What's in your probability of distress, and would I believe 4%?"**
It is the fraction of 5,000 seeded Monte Carlo draws — normal perturbations
on growth (±3pp σ), exit multiple (±0.75x σ) and a parallel base-rate shift
(±1pp σ), all truncated at stated economic bounds — in which the structure
breaches a covenant, runs out of liquidity, or exits with the equity wiped.
It is only as good as those distributions, which are calibrated judgment,
not fitted data — that's why the number is presented next to a deterministic
stress case rather than instead of one. Directionally it behaves correctly:
it is monotone in leverage (tested), near zero at 3x, and explosive past 6x.

**9. "Why grid search — is your optimizer just brute force?"**
Deliberately, yes. Covenant tests cut non-convex holes in the feasible
region, tranche caps kink the cost of funds, and the sweep makes the
objective path-dependent — a gradient method converges to whichever local
optimum it starts near and tells you nothing about the terrain. The 231-point
scan is exhaustive over the discretized space, runs in under a second, and
the surface itself is the deliverable an IC wants to see. The honest
limitation is discretization: the true optimum can sit between grid points,
and the tool flags boundary optima instead of hiding them.

**10. "What would break this model in the real world?"**
Annual granularity hides intra-year liquidity swings; the operating model is
one growth rate rather than a revenue/margin build; there are no management
fees, no minimum cash at close, no interest income; distributions in the
Monte Carlo are judgment; and yfinance EBITDA is not diligence-grade. None
of these flatter the answer — most are conservative — but a live process
would rebuild the operating case from a data room before trusting the
optimizer's exact turn count. The model's job is the structuring logic, and
that part is validated to the penny against a hand-built schedule.
