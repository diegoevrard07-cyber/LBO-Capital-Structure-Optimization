# StackOptimal — LBO Capital-Structure Optimizer

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Tests](https://github.com/diegoevrard07-cyber/LBO-Capital-Structure-Optimization/actions/workflows/tests.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

**An optimizer that searches over LBO capital structures — how much debt, in which
tranches — to maximize the private-equity sponsor's return (IRR), subject to the
constraints that actually kill deals: leverage limits, interest-coverage and
leverage covenants, and survival of a stressed downside.** Point it at any listed
company; it pulls live fundamentals and returns the optimal debt stack, the full
risk-return frontier, and a draft IC memo.

![Deal tearsheet](docs/img/tearsheet.png)

*To re-capture: run the app (Quickstart below), fetch `TSCO.L`, press **Run
optimization**, and screenshot the landing page at ~1440px browser width — the dark
theme is locked in `.streamlit/config.toml`, so output is consistent. Secondary
shots: the optimizer's IRR grid ([docs/img/heatmap.png](docs/img/heatmap.png)) and
the efficient frontier ([docs/img/frontier.png](docs/img/frontier.png)).*

![IRR sensitivity grid](docs/img/heatmap.png)
![Efficient frontier](docs/img/frontier.png)

## Why this exists

I kept hearing the same claim in PE interviews and reading: that returns come from
"buying right, improving operations, and selling well" — with the capital structure
treated as plumbing. But the structure *is* a design choice with its own return
stream: every turn of leverage mechanically amplifies IRR, right up to the point
where covenants, cash adequacy, and downside fragility take the company away from
you. I wanted to know how much of the spread between a good deal and a blown-up deal
is decided at the financing table, before anyone touches operations.

So I built the tool I wanted to interrogate: give it a ticker, and it searches the
whole leverage × tranche-mix space, prices each structure through a year-by-year
debt waterfall, stress-tests it against a recession case, and shows you the gap
between the structure that maximizes headline IRR and the structure that survives.
That gap — usually smaller than people expect, and occasionally fatal — is the
interesting number.

## What it does

The pipeline mirrors how a deal team actually builds an LBO:

1. **Transaction assumptions** — entry at the market EV/EBITDA plus a control
   premium, fees capitalized into uses, 5-year hold, exit at entry multiple (no
   multiple expansion underwritten).
2. **Sources & uses** — the equity cheque is the plug: EV + fees − debt raised.
3. **Multi-tranche debt** — senior TLB (SONIA + 350, capped at 4.0×), second lien
   (+650, +1.5×), mezzanine (12% fixed cash-pay, +1.5×), filled cheapest-first,
   exactly as an arranger would place it. *PIK* ("payment in kind" — interest that
   accrues to the balance instead of being paid in cash) is a documented extension;
   the mezzanine here is cash-pay.
4. **Cash-sweep waterfall** — each year: cash interest on opening balances →
   mandatory amortization → *cash sweep* (75% of remaining free cash flow prepays
   debt, senior-first) → residual builds balance-sheet cash.
5. **FCF projection** — EBITDA growth, capex, cash taxes (interest is deductible),
   working-capital drag, with covenant tests every year: *interest coverage*
   (EBITDA ÷ cash interest ≥ 2.0×) and a stepped net-leverage covenant.
6. **Exit returns** — IRR and *MOIC* (multiple on invested capital: exit equity ÷
   entry equity) on the full cash-flow vector.
7. **Attribution bridge** — equity profit decomposed exactly into EBITDA growth,
   multiple expansion, and debt paydown, so you can see whether a deal earns its
   return or just bets on the multiple.
8. **Constrained optimization + Monte Carlo** — the optimum across the grid, the
   naive max-IRR structure for contrast, and 5,000-draw risk analysis (growth,
   exit multiple, base-rate path) powering the efficient frontier.

The dashboard is a single scrollable **deal tearsheet** (metric strip, IRR heatmap,
cap stack, paydown, attribution, credit grid, frontier, Monte Carlo, waterfall);
the generated IC memo lives on its own page (sidebar navigation) with a markdown
download.

## How the optimizer works

**Objective:** maximize base-case IRR. **Decision space:** total leverage (2.0× →
market capacity, 0.25× steps) × senior mix (50–100%, 5pp steps). **Constraints:** a
structure is admissible only if it is covenant-compliant in every year of the base
case *and* survives the stressed downside (−20% growth, a one-off −10% EBITDA shock,
−1.0× exit multiple) with the equity intact.

**Search method — grid scan, deliberately.** The debt waterfall makes the objective
non-convex and path-dependent — covenant tests kink the feasible region, tranche-cap
boundaries kink the cost of funds, and the cash sweep switches the mapping from this
year's free cash flow to next year's interest bill. Gradient methods on such a
surface silently converge to local optima and say nothing about the surrounding
terrain. A transparent grid scan finds the global optimum over the discretized
space, costs milliseconds per evaluation, and yields the full risk-return surface as
a free by-product.

**Honest limitations of the approach:** the grid discretizes a continuous space, so
the true optimum can sit between grid points (the tool flags boundary optima rather
than hiding them); the stress case is one deterministic scenario, not a statement
about all recessions; and IRR-maximization subject to survival is a sponsor's
objective — a lender or an LP would weight the constraints differently.

## Example result — Tesco plc (TSCO.L)

Live fundamentals at capture: EBITDA £5,054mm, revenue £73.7bn, market EV/EBITDA
9.1×. Entry at 10.1× (market + 1.0× premium) → EV £51,263mm.

| Structure | Leverage | Mix | Base IRR | MOIC | Survives stress? |
|---|---|---|---|---|---|
| **Optimum (survivable)** | **4.75×** | 100% senior | **10.3%** | **1.63×** | **Yes** |
| Naive max-IRR | 6.00× | 100% senior | 10.8% | 1.72× | **No** — coverage breach, year 1 |

The before/after is the money shot: unconstrained IRR-maximization picks 6.00× and
breaches its interest-coverage covenant in the first stressed year (1.85× vs the
2.0× minimum). The survivable optimum gives up just **0.5pp of IRR** to keep the
company. At the optimum: Monte Carlo median IRR 10.4% (P5/P95 2.5%/18.3%),
P(distress) 4.4%, and the £18.1bn equity profit decomposes into £11.4bn EBITDA
growth + £8.0bn debt paydown + £0 multiple expansion − £1.4bn fees.

*(Figures move with live market data; re-run for the current answer.)*

## Quickstart

```bash
git clone https://github.com/diegoevrard07-cyber/LBO-Capital-Structure-Optimization.git
cd LBO-Capital-Structure-Optimization
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The app ships pre-loaded with the Tesco (`TSCO.L`) sample configuration — press
**Fetch company**, then **Run optimization**; the full grid scan + Monte Carlo +
frontier takes ~20–30 seconds. Tests: `pytest` (11 engine tests, fully offline).
Convexity demo: `python -m src.debt`. Requires Python 3.11+.

If your shell can't find `pytest`/`streamlit` outside the venv, use the
`python3 -m …` forms (`python3 -m streamlit run app.py`, `python3 -m pytest`) — they
work regardless of `PATH`.

## Design decisions I'd defend in an interview

- **Simulation over closed form** — the waterfall is path-dependent (this year's
  sweep sets next year's interest, hence taxes, hence next year's sweep), so the
  engine computes year by year, like the Excel model it would be audited against.
- **Grid search over gradients** — covenant kinks and tranche-cap boundaries make
  the surface non-convex; the grid is global, transparent, and the frontier falls
  out for free.
- **Admissibility = base feasibility AND stress survival** — the naive max-IRR
  structure is reported separately, so the price of survivability is always visible.
- **Every data fallback is disclosed in the UI** — public-filings data is noisy;
  silently substituting an assumption would be intellectually dishonest.
- **Fixed Monte Carlo seeds** — a research tool must return identical numbers
  run-to-run, or its outputs can't be cited.

## Limitations & roadmap

- **No revolver** — a cash shortfall is immediately fatal; extension: an RCF with
  drawn cost and commitment fee, repaid first in the sweep.
- **No PIK interest** — mezzanine is cash-pay; extension: a `pik_fraction` per
  tranche, accruing to the balance and excluded from cash coverage.
- **D&A = capex** (steady state) — extension: a depreciation schedule with lags for
  lumpy capex businesses.
- **No dividend recaps** — the IRR solver already handles multiple sign changes;
  extension: an optional year-3 relever into the cash-flow vector.
- **Single currency, no tax-loss carry-forwards** — both bounded, documented
  simplifications.
- **Public data only** — paid loan-market data (PitchBook LCD) deliberately unused;
  defaults are chosen to be defensible against free public sources (Bank of England
  rate data, S&P Global leveraged-finance commentary, listed direct-lender
  disclosures).

The full mechanism-by-mechanism write-up — waterfall mechanics, covenant practice,
the value-creation bridge, every default's justification — is in
[docs/WALKTHROUGH.md](docs/WALKTHROUGH.md). The IC memo structure the app generates
is in [docs/memo_template.md](docs/memo_template.md).

## Repo layout

```
src/inputs.py     data fetch (yfinance) + documented market assumptions
src/debt.py       tranche objects + cheapest-first stack builder
src/model.py      deterministic LBO engine (the core)
src/optimizer.py  grid search + naive-vs-survivable gap
src/risk.py       Monte Carlo + efficient frontier
src/memo.py       IC memo generator
app.py            terminal-styled Streamlit dashboard (single-page tearsheet)
pages/            IC memo page (sidebar navigation)
tests/            pytest suite (11 tests, fully offline)
docs/             WALKTHROUGH.md, memo template, screenshots
```

## Disclaimer

Personal, educational project. Not investment advice, not an offer, and not a
substitute for a real diligence process. Market data via Yahoo Finance; financing
assumptions are illustrative defaults, adjustable in the UI.

## License

MIT — see [LICENSE](LICENSE).
