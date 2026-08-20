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
cash available = opening cash + CFADS
  1. cash interest on OPENING balances (all tranches)
  2. mandatory amortization (senior: 1% of original face)
  3. cash sweep: 75% of the remainder, prepaying senior-first
  4. residual accumulates as balance-sheet cash
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
test.

Mechanical details that matter:

- **Interest on opening balances.** European TLB convention; interest on
  average balances would implicitly assume intra-year sweep timing and
  overstate deleveraging.
- **Taxes on (EBIT − interest), floored at zero.** The interest tax shield is
  real and is one of leverage's genuine economic benefits; the floor means we
  never book a tax *refund* in a loss year (no carry-forward modelling — see
  simplifications).
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

A third failure mode is not a covenant at all: a **cash shortfall** (available
cash < interest + mandatory amortization) is a payment default. With no
revolver — a documented simplification — there is no liquidity buffer, so the
model treats it as immediately fatal.

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
| Debt fees | 2.5% of debt | Blended OID/arrangement across tranches |
| Transaction fees | 1.5% of EV | Advisory, legal, diligence — mid-market all-in |

Covenants and structure:

| Assumption | Default | Justification |
|---|---|---|
| Min interest coverage | 2.0x | Standard European covenant test |
| Leverage covenant | entry +0.5x, −0.5x/yr, floor 4.0x | Opening headroom plus expected deleveraging; lenders won't covenant below market |
| Hold | 5 years | Classic PE hold period |
| Exit multiple | = entry multiple | Conservative: no expansion underwritten |
| Entry premium | +1.0x over market | Control premium to win the asset |
| Cash sweep | 75% of post-amort FCF | Market-standard sweep with 25% cash retention |
| Working capital | 2% of incremental revenue | Asset-light norm |
| Stress case | −20% growth, −10% year-1 EBITDA, −1.0x exit | A recession year that permanently resets the earnings base |
| MC sigmas | growth 3pp, multiple 0.75x, rate 1pp | One-standard-deviation misses calibrated to cycle history |
| MC draws / seed | 5,000 / 42 | P5/P95 stable to ~0.1pp; fixed seed for reproducibility |

---

## 10. Known simplifications, and how each would be extended

- **No revolver.** A cash shortfall is immediately fatal; in practice a
  revolving credit facility absorbs timing gaps. *Extension: add an RCF line
  with a drawn-cost and commitment fee, drawn automatically on shortfall and
  repaid first in the sweep.*
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
