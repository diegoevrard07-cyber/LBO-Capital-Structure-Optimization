"""Data ingestion and market assumptions for the LBO optimizer.

Two responsibilities:

1. ``fetch_company`` pulls the minimum fundamental dataset needed to underwrite
   an LBO of any listed target from Yahoo Finance (via ``yfinance``). Public
   filings data is messy: line items are missing, renamed, or reported under
   different taxonomies across jurisdictions. Every fallback taken is recorded
   in ``CompanyData.warnings`` — an IC would rather see an explicit assumption
   than a silently substituted number.

2. ``MarketAssumptions`` holds every financing and deal assumption with
   defaults calibrated to the European leveraged-finance market as of
   mid-2026 (SONIA-based floating rates, TLB-style senior debt, second-lien
   and mezzanine junior layers). Each field documents its real-world
   justification; there are no magic numbers anywhere else in the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Sanity bounds for fetched fundamentals. Outside these bands the number is
# almost certainly a data artefact (or a business an LBO would never touch),
# so we warn rather than fail.
# ---------------------------------------------------------------------------
EV_EBITDA_SANITY_LOW = 3.0  # below 3x the market is pricing distress or a data error
EV_EBITDA_SANITY_HIGH = 30.0  # above 30x is growth-equity territory, not leveraged buyout
TAX_RATE_FLOOR = 0.15  # below this, "effective rate" reflects one-off items, not steady state
TAX_RATE_CAP = 0.30  # above this, the same; UK mainstream corporation tax is 25%
TAX_RATE_DEFAULT = 0.25  # UK corporation tax main rate (Finance Act 2021)
GROWTH_CAP = 0.15  # no LBO underwriting should extrapolate >15% top-line growth for 5 years
CAPEX_PCT_CAP = 0.30  # >30% of revenue is heavy industry; cap to keep CFADS sensible


@dataclass
class CompanyData:
    """Fundamental snapshot of the target, with a full audit trail.

    ``warnings`` records every fallback, cap, or assumption applied during
    ingestion. The UI surfaces these prominently: intellectual honesty about
    data quality is a feature, not an embarrassment.
    """

    ticker: str
    name: str
    currency: str
    ebitda: float  # latest full-year EBITDA, in millions of `currency`
    revenue: float  # latest full-year revenue, in millions
    ev_ebitda: float  # current market EV / EBITDA (entry-multiple anchor)
    capex_pct: float  # capex as % of revenue, 3-year average
    tax_rate: float  # effective cash tax rate, 3-year average, bounded
    growth: float  # base-case EBITDA growth proxy (3yr revenue CAGR, capped)
    warnings: list[str] = field(default_factory=list)


@dataclass
class MarketAssumptions:
    """Every financing and deal assumption, with real-world justification.

    Defaults reflect the European leveraged-finance market as of mid-2026:
    a SONIA-based floating-rate environment, unitranche/TLB senior paper,
    second-lien and mezzanine junior layers, and UK-style covenant practice.
    """

    # --- Rates (SONIA proxy + margins) ---
    base_rate: float = 0.0375  # SONIA proxy mid-2026; BoE Bank Rate path after 2024-25 cuts
    senior_margin: float = (
        0.0350  # TLB at S+350: typical large-cap European institutional term loan
    )
    second_lien_margin: float = (
        0.0650  # S+650: ~300bp over senior is standard second-lien economics
    )
    mezz_rate: float = (
        0.1200  # 12% fixed cash-pay mezzanine (PIK toggle documented as an extension)
    )

    # --- Capacity caps (turns of EBITDA) ---
    senior_cap: float = (
        4.0  # senior capacity cap: 4.0x is the post-2022 European norm for solid credits
    )
    second_lien_cap: float = 1.5  # +1.5x second lien on top of senior
    mezz_cap: float = 1.5  # +1.5x mezzanine on top of that; total capacity 7.0x

    # --- Amortization ---
    senior_amort_pct: float = 0.01  # 1%/yr mandatory amort: TLB market standard, remainder bullet
    second_lien_amort_pct: float = 0.0  # second lien is bullet-only in European practice
    mezz_amort_pct: float = 0.0  # mezzanine is bullet-only; repaid at exit/refi

    # --- Fees (capitalized into Uses) ---
    debt_fee_pct: float = 0.025  # 2.5% OID/arrangement on debt raised (blended across tranches)
    ev_fee_pct: float = 0.015  # 1.5% of EV for advisory/legal/diligence — typical mid-market all-in

    # --- Covenants ---
    min_interest_coverage: float = 2.0  # EBITDA / cash interest >= 2.0x: standard European cov test
    leverage_covenant_headroom: float = 0.5  # opening covenant set at entry leverage + 0.5x
    leverage_stepdown: float = 0.5  # covenant steps down 0.5x/yr as the deal is expected to delever
    leverage_floor: float = (
        4.0  # ...but never below 4.0x: lenders won't covenant below market leverage
    )

    # --- Deal structure ---
    hold_years: int = 5  # classic 5-year PE hold
    exit_multiple_premium: float = (
        0.0  # exit = entry multiple (conservative: no multiple expansion assumed)
    )
    entry_premium_turns: float = (
        1.0  # +1.0x over market EV/EBITDA: takeover premium to win the asset
    )
    cash_sweep_pct: float = 0.75  # 75% of post-amort FCF sweeps to debt prepayment; 25% builds cash
    wc_pct_of_delta_rev: float = (
        0.02  # working capital absorbs 2% of incremental revenue (asset-light norm)
    )

    # --- Stress case (downside underwriting) ---
    stress_growth_haircut: float = 0.20  # stressed growth = 80% of base (a 20% haircut)
    stress_ebitda_shock: float = 0.10  # one-off -10% EBITDA hit in year 1 (recession year)
    stress_exit_multiple_shock: float = 1.0  # exit multiple compresses 1.0 turn in the downside

    # --- Monte Carlo ---
    mc_growth_sigma: float = 0.03  # 3pp absolute sigma on growth: ~1-in-6 chance of a 3pp miss
    mc_multiple_sigma: float = (
        0.75  # 0.75x sigma on exit multiple: one standard deviation of cycle swing
    )
    mc_rate_sigma: float = 0.01  # 1pp sigma on the base-rate path for floating tranches
    mc_draws: int = 5000  # enough draws that P5/P95 are stable to ~0.1pp
    mc_seed: int = 42  # fixed seed: a research tool must be reproducible run-to-run


# ---------------------------------------------------------------------------
# yfinance helpers — every accessor degrades gracefully and records a warning.
# ---------------------------------------------------------------------------


def _row(frame: pd.DataFrame, *names: str) -> pd.Series | None:
    """Return the first matching row of a financials frame, else ``None``.

    yfinance row labels vary by reporter and version (e.g. "Operating Income"
    vs "EBIT"), so we try a list of aliases.
    """
    if frame is None or frame.empty:
        return None
    for name in names:
        if name in frame.index:
            series = frame.loc[name].dropna()
            if not series.empty:
                return series
    return None


def _latest(series: pd.Series | None) -> float | None:
    """Most recent non-null value of a financials row (columns are newest-first)."""
    if series is None or series.empty:
        return None
    return float(series.iloc[0])


def _cagr(first: float, last: float, years: int) -> float | None:
    """Compound annual growth rate, guarding against sign changes and zeros."""
    if first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1.0


def fetch_company(ticker: str) -> CompanyData:
    """Fetch and assemble the underwriting dataset for ``ticker``.

    Raises ``ValueError`` only when the target is fundamentally unusable
    (unknown ticker, or no way to derive EBITDA). Everything else degrades
    to a documented assumption recorded in ``warnings``.
    """
    import yfinance as yf  # imported lazily so the engine runs offline in tests

    warnings: list[str] = []
    tk = yf.Ticker(ticker)

    try:
        info = tk.info
    except Exception as exc:  # network failure, rate limit, delisted ticker
        raise ValueError(f"Could not fetch data for ticker '{ticker}': {exc}") from exc

    # A valid quote is identified by quoteType/symbol/market data; display
    # names can be stripped from a rate-limited response, so their absence
    # is a warning, not a rejection.
    if (
        not info
        or info.get("quoteType") not in ("EQUITY", "ETF", "MUTUALFUND", "INDEX")
        and not info.get("marketCap")
    ):
        raise ValueError(
            f"Ticker '{ticker}' returned no company profile — check the symbol "
            "(include the exchange suffix, e.g. 'TSCO.L' for London)."
        )

    name = info.get("longName") or info.get("shortName") or ticker.upper()
    if "longName" not in info and "shortName" not in info:
        warnings.append(
            f"Company name not returned (rate-limited response); displaying ticker '{ticker.upper()}'."
        )
    currency = info.get("financialCurrency") or info.get("currency") or "USD"
    if "financialCurrency" not in info:
        warnings.append(f"Reporting currency not disclosed; assumed {currency}.")

    financials = tk.financials  # annual income statement
    cashflow = tk.cashflow  # annual cash-flow statement

    # --- Revenue ---
    revenue_row = _row(financials, "Total Revenue", "Operating Revenue")
    revenue = _latest(revenue_row)
    if revenue is None or revenue <= 0:
        raise ValueError(f"'{ticker}' reports no usable revenue line — cannot underwrite an LBO.")

    # --- EBITDA: prefer the reported line, else operating income + D&A ---
    ebitda = _latest(_row(financials, "EBITDA", "Normalized EBITDA"))
    if ebitda is None:
        operating_income = _latest(_row(financials, "Operating Income", "EBIT"))
        da = _latest(
            _row(
                cashflow,
                "Depreciation And Amortization",
                "Depreciation Amortization Depletion",
                "Depreciation",
            )
        )
        if operating_income is None:
            raise ValueError(
                f"'{ticker}' has no EBITDA and no operating income — cannot derive EBITDA."
            )
        if da is None:
            da = 0.0
            warnings.append(
                "D&A not reported; EBITDA approximated as operating income only "
                "(conservative — understates debt capacity)."
            )
        ebitda = operating_income + da
        warnings.append("EBITDA not reported directly; derived as operating income + D&A.")
    if ebitda <= 0:
        raise ValueError(f"'{ticker}' has non-positive EBITDA ({ebitda:,.0f}) — not LBO-able.")

    # --- EV / EBITDA: prefer the market-implied ratio, else build EV from parts ---
    ev_ebitda = info.get("enterpriseToEbitda")
    if ev_ebitda is None or not np.isfinite(ev_ebitda) or ev_ebitda <= 0:
        market_cap = info.get("marketCap")
        total_debt = info.get("totalDebt")
        cash = info.get("totalCash") or 0.0
        if market_cap and total_debt is not None:
            ev_ebitda = (market_cap + total_debt - cash) / ebitda
            warnings.append(
                "EV/EBITDA not quoted; computed as (market cap + debt - cash) / EBITDA."
            )
        else:
            ev_ebitda = 10.0
            warnings.append(
                "Could not derive market EV/EBITDA; defaulted to 10.0x — "
                "review the entry multiple before relying on results."
            )
    ev_ebitda = float(ev_ebitda)
    if not (EV_EBITDA_SANITY_LOW <= ev_ebitda <= EV_EBITDA_SANITY_HIGH):
        warnings.append(
            f"Market EV/EBITDA of {ev_ebitda:.1f}x is outside the {EV_EBITDA_SANITY_LOW:.0f}x-"
            f"{EV_EBITDA_SANITY_HIGH:.0f}x sanity band — treat entry pricing with care."
        )

    # --- Capex intensity: 3-year average of |capex| / revenue ---
    capex_row = _row(cashflow, "Capital Expenditure", "Capital Expenditures")
    capex_pct: float | None = None
    if capex_row is not None and revenue_row is not None:
        ratios = []
        for date in capex_row.index[:3]:
            rev = revenue_row.get(date)
            cap = capex_row.get(date)
            if rev and rev > 0 and pd.notna(cap):
                ratios.append(abs(float(cap)) / float(rev))
        if ratios:
            capex_pct = float(np.mean(ratios))
    if capex_pct is None:
        capex_pct = 0.04
        warnings.append("Capex history unavailable; assumed 4% of revenue (asset-light default).")
    if capex_pct > CAPEX_PCT_CAP:
        warnings.append(
            f"Capex intensity of {capex_pct:.0%} exceeds {CAPEX_PCT_CAP:.0%} — "
            "capped to keep the debt-sizing cash flows meaningful."
        )
        capex_pct = CAPEX_PCT_CAP

    # --- Effective tax rate: 3-year average, bounded to a sane band ---
    tax_row = _row(financials, "Tax Provision", "Income Tax Expense")
    pretax_row = _row(financials, "Pretax Income", "Income Before Tax")
    tax_rate: float | None = None
    if tax_row is not None and pretax_row is not None:
        rates = []
        for date in tax_row.index[:3]:
            tax, pretax = tax_row.get(date), pretax_row.get(date)
            if pd.notna(tax) and pd.notna(pretax) and pretax > 0 and tax >= 0:
                rates.append(float(tax) / float(pretax))
        if rates:
            tax_rate = float(np.mean(rates))
    if tax_rate is None:
        tax_rate = TAX_RATE_DEFAULT
        warnings.append(
            f"Tax history unusable; defaulted to the UK main corporation-tax rate of {TAX_RATE_DEFAULT:.0%}."
        )
    elif tax_rate < TAX_RATE_FLOOR or tax_rate > TAX_RATE_CAP:
        clamped = min(max(tax_rate, TAX_RATE_FLOOR), TAX_RATE_CAP)
        warnings.append(
            f"Effective tax rate of {tax_rate:.0%} reflects one-off items; "
            f"clamped to {clamped:.0%} (band {TAX_RATE_FLOOR:.0%}-{TAX_RATE_CAP:.0%})."
        )
        tax_rate = clamped

    # --- Growth proxy: 3-year revenue CAGR, capped ---
    growth: float | None = None
    if revenue_row is not None and len(revenue_row) >= 4:
        newest, oldest = float(revenue_row.iloc[0]), float(revenue_row.iloc[3])
        growth = _cagr(oldest, newest, 3)
    if growth is None:
        growth = 0.03
        warnings.append("Revenue history too short for a CAGR; assumed 3% nominal growth.")
    if growth > GROWTH_CAP:
        warnings.append(
            f"Revenue CAGR of {growth:.0%} exceeds the {GROWTH_CAP:.0%} underwriting cap; capped."
        )
        growth = GROWTH_CAP
    if growth < 0:
        warnings.append(
            f"Revenue CAGR is negative ({growth:.0%}); a declining business rarely clears "
            "LBO debt service — expect few admissible structures."
        )

    # Normalize to millions: the unit every downstream table and memo uses.
    return CompanyData(
        ticker=ticker.upper(),
        name=str(name),
        currency=str(currency),
        ebitda=float(ebitda) / 1e6,
        revenue=float(revenue) / 1e6,
        ev_ebitda=ev_ebitda,
        capex_pct=float(capex_pct),
        tax_rate=float(tax_rate),
        growth=float(growth),
        warnings=warnings,
    )
