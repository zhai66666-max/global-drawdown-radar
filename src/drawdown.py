from __future__ import annotations
"""
Drawdown Computation Engine
All metrics computed with expanding window — NO future data leak.
Uses Adjusted Close for long-term drawdowns (dividend/split adjusted).
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from src.config import (
    ETF_LOOKUP,
    ETFS,
    TRADING_DAYS_52W,
    TRADING_DAYS_5Y,
    VOLATILITY_WINDOW,
)

logger = logging.getLogger(__name__)

# ─── Core drawdown primitives ──────────────────────────────────────────────────


def compute_drawdown_series(prices: pd.Series) -> pd.Series:
    """
    Daily drawdown from rolling expanding peak.
    dd[t] = price[t] / cummax(price[0..t]) - 1
    Uses expanding cummax — NO future data leak.
    Returns negative fractions (e.g., -0.15 = -15% drawdown).
    """
    running_max = prices.expanding().max()
    return prices / running_max - 1.0


def compute_running_max_drawdown(drawdown_series: pd.Series) -> pd.Series:
    """
    Running maximum drawdown experienced up to each date.
    running_max_dd[t] = min(drawdown[0..t])
    Last value = all-time max drawdown.
    """
    return drawdown_series.expanding().min()


# ─── Single-ticker metrics ─────────────────────────────────────────────────────


def _safe_last(series: pd.Series) -> Optional[float]:
    """Return last non-NaN value as float, or None."""
    clean = series.dropna()
    if clean.empty:
        return None
    return float(clean.iloc[-1])


def compute_current_price_metrics(
    prices: pd.Series, latest_unadj_close: Optional[float] = None
) -> dict:
    """Latest price, daily change, data-as-of date."""
    clean = prices.dropna()
    if clean.empty:
        return {"current_price": None, "daily_change_pct": None, "data_date": None}

    if latest_unadj_close is not None and latest_unadj_close > 0:
        current_price = latest_unadj_close
    else:
        current_price = float(clean.iloc[-1])

    prev_price = float(clean.iloc[-2]) if len(clean) >= 2 else current_price
    daily_change_pct = (current_price / prev_price - 1.0) if prev_price > 0 else None
    data_date = str(clean.index[-1].date())

    return {
        "current_price": round(current_price, 2),
        "daily_change_pct": round(daily_change_pct, 4) if daily_change_pct is not None else None,
        "data_date": data_date,
    }


def compute_52w_drawdown(prices: pd.Series) -> Optional[float]:
    """Current price vs 52-week (252 trading day) high."""
    window = prices.iloc[-TRADING_DAYS_52W:]
    if len(window) < 20:
        return None
    peak = window.max()
    cur = window.iloc[-1]
    if peak <= 0:
        return None
    return float(cur / peak - 1.0)


def compute_52w_max_drawdown(prices: pd.Series) -> Optional[float]:
    """Maximum drawdown within the last 52-week window."""
    window = prices.iloc[-TRADING_DAYS_52W:]
    if len(window) < 20:
        return None
    running_max = window.expanding().max()
    dd = window / running_max - 1.0
    return float(dd.min())


def compute_5y_drawdown(prices: pd.Series) -> Optional[float]:
    """Current price vs 5-year high."""
    window = prices.iloc[-TRADING_DAYS_5Y:]
    if len(window) < 20:
        return None
    peak = window.max()
    cur = window.iloc[-1]
    if peak <= 0:
        return None
    return float(cur / peak - 1.0)


def compute_historical_drawdown(prices: pd.Series) -> Optional[float]:
    """Current price vs all-time high."""
    peak = prices.max()
    cur = prices.iloc[-1]
    if peak <= 0:
        return None
    return float(cur / peak - 1.0)


def compute_historical_max_drawdown(prices: pd.Series) -> Optional[float]:
    """All-time maximum drawdown."""
    dd = compute_drawdown_series(prices)
    return float(dd.min())


def compute_drawdown_percentile(prices: pd.Series) -> Optional[float]:
    """
    Where does today's drawdown rank in history?
    Uses expanding cummax for every day — no future leak.
    Returns 0-100. Higher = current dd is worse (more extreme) than more of history.
    E.g., 95 means current drawdown exceeds 95% of historical drawdown days.
    """
    dd_series = compute_drawdown_series(prices)
    if dd_series.empty:
        return None
    today_dd = dd_series.iloc[-1]
    # Percentile: fraction of days with dd <= today's dd
    pct = (dd_series <= today_dd).mean() * 100
    return round(float(pct), 1)


def compute_current_cycle_max_drawdown(prices: pd.Series) -> Optional[float]:
    """
    Max drawdown in current cycle (since last all-time high).
    Cycle resets when price makes a new ATH.
    """
    ath_idx = prices.idxmax()
    if ath_idx is None:
        return None
    # Drawdown from the ATH to the lowest point since then
    cycle = prices.loc[ath_idx:]
    if len(cycle) < 2:
        return 0.0
    running_max = cycle.expanding().max()
    dd = cycle / running_max - 1.0
    return float(dd.min())


def compute_20d_annualized_volatility(prices: pd.Series) -> Optional[float]:
    """20-day annualized volatility from daily log returns."""
    clean = prices.dropna()
    if len(clean) < VOLATILITY_WINDOW:
        return None
    recent = clean.iloc[-VOLATILITY_WINDOW:]
    log_returns = np.log(recent / recent.shift(1)).dropna()
    if len(log_returns) < 10:
        return None
    daily_std = float(log_returns.std())
    annual_vol = daily_std * np.sqrt(252)
    return round(annual_vol, 4)


def compute_distance_from_ath(prices: pd.Series) -> tuple[Optional[float], Optional[str], Optional[int]]:
    """
    Returns (distance_from_ath_pct, ath_date, days_since_ath).
    distance_from_ath = current / ath - 1.0  (negative fraction).
    """
    ath_idx = prices.idxmax()
    ath_val = prices.max()
    cur = prices.iloc[-1]
    if ath_val <= 0:
        return None, None, None

    dd = float(cur / ath_val - 1.0)
    ath_date = str(ath_idx.date())
    days_since = int((pd.Timestamp.now(tz=ath_idx.tz) - ath_idx).days) if hasattr(ath_idx, 'tz') else int((pd.Timestamp.now() - ath_idx).days)

    return round(dd, 4), ath_date, days_since


# ─── Orchestrator ──────────────────────────────────────────────────────────────


def compute_all_metrics(
    adj_close: pd.DataFrame,
    latest_close: dict[str, float],
) -> list[dict]:
    """
    Compute all metrics for all ETFs.

    Args:
      adj_close: DataFrame (dates × tickers) of Adjusted Close prices
      latest_close: dict of {ticker: latest_unadjusted_close}

    Returns:
      List of dicts, one per ETF, with all metrics. Sorted by ETF_LOOKUP order.
    """
    results = []

    for ticker in adj_close.columns:
        try:
            prices = adj_close[ticker].dropna()
            if len(prices) < 10:
                logger.warning("%s: insufficient data (%d rows)", ticker, len(prices))
                continue

            etf_info = ETF_LOOKUP.get(ticker, {"name_cn": ticker, "market": ""})
            unadj_close = latest_close.get(ticker)

            # Price basics
            price_info = compute_current_price_metrics(prices, unadj_close)

            # Drawdowns
            dd_52w = compute_52w_drawdown(prices)
            max_dd_52w = compute_52w_max_drawdown(prices)
            dd_5y = compute_5y_drawdown(prices)
            dd_historical = compute_historical_drawdown(prices)
            max_dd_historical = compute_historical_max_drawdown(prices)
            dd_percentile = compute_drawdown_percentile(prices)
            cycle_max_dd = compute_current_cycle_max_drawdown(prices)
            vol_20d = compute_20d_annualized_volatility(prices)
            dist_from_ath, ath_date, days_since_ath = compute_distance_from_ath(prices)

            # Status
            from src.config import get_drawdown_status
            status_label, status_color, status_emoji = get_drawdown_status(dd_historical)

            results.append({
                "ticker": ticker,
                "name_cn": etf_info["name_cn"],
                "market": etf_info["market"],
                "current_price": price_info["current_price"],
                "daily_change_pct": price_info["daily_change_pct"],
                "data_date": price_info["data_date"],
                "dd_52w": dd_52w,
                "max_dd_52w": max_dd_52w,
                "dd_5y": dd_5y,
                "dd_historical": dd_historical,
                "max_dd_historical": max_dd_historical,
                "dd_percentile": dd_percentile,
                "cycle_max_dd": cycle_max_dd,
                "vol_20d": vol_20d,
                "dist_from_ath": dist_from_ath,
                "ath_date": ath_date,
                "days_since_ath": days_since_ath,
                "status_label": status_label,
                "status_color": status_color,
                "status_emoji": status_emoji,
                "error": None,
            })

        except Exception as exc:
            logger.error("%s: metric computation failed: %s", ticker, exc)
            etf_info = ETF_LOOKUP.get(ticker, {"name_cn": ticker, "market": ""})
            results.append({
                "ticker": ticker,
                "name_cn": etf_info["name_cn"],
                "market": etf_info["market"],
                "current_price": None,
                "daily_change_pct": None,
                "data_date": None,
                "dd_52w": None, "max_dd_52w": None, "dd_5y": None,
                "dd_historical": None, "max_dd_historical": None,
                "dd_percentile": None, "cycle_max_dd": None,
                "vol_20d": None, "dist_from_ath": None,
                "ath_date": None, "days_since_ath": None,
                "status_label": "N/A", "status_color": "#9ca3af", "status_emoji": "⚪",
                "error": str(exc),
            })

    # Sort by ETF definition order
    ticker_order = {e["ticker"]: i for i, e in enumerate(ETFS)}
    results.sort(key=lambda r: ticker_order.get(r["ticker"], 99))

    logger.info("Metrics computed for %d/%d ETFs", len(results), len(adj_close.columns))
    return results
