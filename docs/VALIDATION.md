# Hand-worked validation of the engine

Before trusting an optimizer, prove the engine under it. This document builds one
LBO **entirely by hand** — every year of the debt schedule, then IRR and MOIC —
and reconciles the engine to it. The same numbers are pinned in
`tests/test_model.py::test_engine_reproduces_hand_worked_schedule`, so any future
change that moves a cash flow by a penny fails CI.

## The deal (all assumptions stated)

Synthetic target "TestCo plc" — chosen synthetic so every input is round and the
arithmetic is checkable on paper:

| Input | Value |
|---|---|
| Entry EBITDA | 100.0 |
| Revenue | 1,000.0 |
| EBITDA growth | 3.0%/yr |
| Capex | 4.0% of revenue |
| Working capital | 2.0% of incremental revenue |
| Tax rate | 25.0% |
| Market EV/EBITDA | 8.0x → entry at **9.0x** (+1.0x control premium) |
| Hold | 5 years, exit at entry multiple (9.0x) |
| Structure | **4.5x** total: Senior TLB 4.0x + Second Lien 0.5x, 100% senior mix |
| Senior TLB | SONIA 3.75% + 350bp = **7.25%**, 1%/yr amortization of original face |
| Second Lien | SONIA + 650bp = **10.25%**, bullet |
| Revolver | 0.5x (50.0) committed, undrawn; 50bp commitment fee = **0.25/yr** |
| Fees | 2.5% of debt (11.25) + 1.5% of EV (13.50) = **24.75**, financing fees amortized 11.25/5 = **2.25/yr** for tax |
| Cash sweep | 75% of each year's excess cash flow, senior-first |

**Sources & uses at close.** EV = 9.0 × 100 = 900.00. Uses = 900.00 + 24.75 fees
= 924.75. Sources = 450.00 debt + equity. **Equity cheque = 924.75 − 450.00 =
474.75.** Sources = uses by construction.

## Year 1, fully expanded (beginning-of-period interest convention)

```
EBITDA        = 100 × 1.03            = 103.0000
Capex         = 4% × 1,030            =  41.2000
ΔWC           = 2% × (1,030 − 1,000)  =   0.6000
Interest      = 400×7.25% + 50×10.25% + 0.25 RCF fee
              = 29.00 + 5.125 + 0.25  =  34.3750   (on OPENING balances)
Taxable       = EBIT − interest − fee amortization
              = (103 − 41.2) − 34.375 − 2.25 = 25.175
Taxes         = 25% × 25.175          =   6.2937
CFADS         = 103 − 41.2 − 6.2937 − 0.6 = 54.9062
Mandatory amort = 1% × 400            =   4.0000
Excess cash flow = 54.9062 − 34.375 − 4 = 16.5312
Sweep (75%, senior-first)             =  12.3984
Senior closing = 400 − 4 − 12.3984    = 383.6016
Retained cash  = 25% × 16.5312        =   4.1328
Net debt      = 383.6016 + 50 − 4.1328 = 429.4688
```

## The full schedule (same arithmetic, years 2–5)

| Yr | EBITDA | Capex | ΔWC | Interest | Taxes | CFADS | Amort | ECF | Sweep | Senior | 2L | Cash | Net debt |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 103.0000 | 41.2000 | 0.6000 | 34.3750 | 6.2937 | 54.9062 | 4.00 | 16.5312 | 12.3984 | 383.6016 | 50.00 | 4.1328 | 429.4688 |
| 2 | 106.0900 | 42.4360 | 0.6180 | 33.1861 | 7.0545 | 55.9815 | 4.00 | 18.7954 | 14.0966 | 365.5050 | 50.00 | 8.8317 | 406.6733 |
| 3 | 109.2727 | 43.7091 | 0.6365 | 31.8741 | 7.8599 | 57.0672 | 4.00 | 21.1931 | 15.8948 | 345.6102 | 50.00 | 14.1299 | 381.4802 |
| 4 | 112.5509 | 45.0204 | 0.6556 | 30.4317 | 8.7122 | 58.1627 | 4.00 | 23.7310 | 17.7982 | 323.8120 | 50.00 | 20.0627 | 353.7493 |
| 5 | 115.9274 | 46.3710 | 0.6753 | 28.8514 | 9.6138 | 59.2674 | 4.00 | 26.4160 | 19.8120 | 300.0000 | 50.00 | 26.6667 | 323.3333 |

Checks along the way: interest falls every year as the sweep bites (34.38 →
28.85); the second lien receives no sweep while senior remains (it never does
here); the balance identity opening − amort − sweep = closing holds in every row.

## Exit and returns

```
Exit EBITDA   = 100 × 1.03^5 = 115.9274
Exit EV       = 9.0 × 115.9274 = 1,043.3467
Net debt      = 300.0000 + 50.0000 − 26.6667 = 323.3333
Exit equity   = 1,043.3467 − 323.3333 = 720.0134

MOIC = 720.0134 / 474.75 = 1.516616x
IRR: cash-flow vector [−474.75, 0, 0, 0, 0, +720.0134]
     solve NPV = 0  →  IRR = 1.516616^(1/5) − 1 = 8.686379%
```

(With a single entry and a single exit flow the vector IRR coincides with
MOIC^(1/5) − 1; the engine still solves the polynomial, so the identity is a
check, not the method.)

## Reconciliation

The engine (`run_lbo` on the identical inputs) reproduces **every cell of the
table above to 4 decimal places**, and:

| Metric | Hand | Engine |
|---|---:|---:|
| Equity cheque | 474.7500 | 474.7500 |
| Exit equity | 720.0134 | 720.0134 |
| MOIC | 1.516616x | 1.516616x |
| IRR | 8.686379% | 8.686379% |

Run it yourself: `python3 -m pytest tests/test_model.py::test_engine_reproduces_hand_worked_schedule -v`.
