from __future__ import annotations
"""
Data Fetcher — yfinance batch download with retry, NaN handling, per-ETF isolation.
"""

import time
import logging
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from src.config import ETF_TICKERS

logger = logging.getLogger(__name__)

# Retry config
YFINANCE_RETRIES = 3
YFINANCE_RETRY_DELAY = 5  # seconds

# ─── Public API ────────────────────────────────────────────────────────────────


def fetch_all_etfs(
    tickers: list[str] | None = None,
    period: str = "max",
    max_retries: int = 3,
) -> tuple[pd.DataFrame, dict[str, str], dict[str, float]]:
    """
    Batch-download historical data for all ETFs.

    Returns:
      adj_close: DataFrame (dates × tickers) of Adjusted Close prices
      errors:    dict of {ticker: error_message} for failed tickers
      latest_close: dict of {ticker: latest_unadjusted_close_price}
    """
    if tickers is None:
        tickers = ETF_TICKERS

    ticker_str = " ".join(tickers)
    logger.info("Fetching %d ETFs: %s", len(tickers), ticker_str)

    # ── Retry loop ─────────────────────────────────────────────────────────
    for attempt in range(1, max_retries + 1):
        try:
            data = yf.download(
                ticker_str,
                period=period,
                auto_adjust=False,  # Get both Close and Adj Close
                progress=False,
                group_by="ticker",
            )
            break
        except Exception as exc:
            logger.warning("Download attempt %d/%d failed: %s", attempt, max_retries, exc)
            if attempt < max_retries:
                wait = YFINANCE_RETRY_DELAY * attempt
                logger.info("Retrying in %d seconds...", wait)
                time.sleep(wait)
            else:
                raise RuntimeError(f"yfinance download failed after {max_retries} attempts") from exc

    # ── Parse multi-ticker DataFrame ────────────────────────────────────────
    adj_close = pd.DataFrame()
    errors = {}
    latest_close = {}

    for t in tickers:
        try:
            if len(tickers) == 1:
                # Single ticker: no MultiIndex columns
                df_t = data.copy()
            else:
                if t not in data.columns.levels[0] if hasattr(data.columns, 'levels') else t not in data.columns:
                    errors[t] = "No data returned"
                    continue
                df_t = data[t].copy()

            # Get Adjusted Close
            if "Adj Close" in df_t.columns:
                series = df_t["Adj Close"].dropna()
            elif "Close" in df_t.columns:
                series = df_t["Close"].dropna()
            else:
                errors[t] = "No Close/Adj Close columns"
                continue

            if len(series) < 5:
                errors[t] = f"Insufficient data ({len(series)} rows)"
                continue

            series.name = t
            if adj_close.empty:
                adj_close = pd.DataFrame(series)
            else:
                adj_close = adj_close.join(series, how="outer")

            # Get latest unadjusted close for current price display
            if "Close" in df_t.columns:
                raw_close = df_t["Close"].dropna()
                if len(raw_close) > 0:
                    latest_close[t] = float(raw_close.iloc[-1])

        except Exception as exc:
            errors[t] = f"Parse error: {exc}"
            logger.warning("Failed to parse %s: %s", t, exc)

    # ── Clean up ────────────────────────────────────────────────────────────
    if not adj_close.empty:
        adj_close = adj_close.sort_index()
        adj_close = adj_close.ffill()  # Forward-fill missing days

    logger.info(
        "Fetch complete: %d/%d ETFs OK, %d errors",
        len(adj_close.columns) if not adj_close.empty else 0,
        len(tickers),
        len(errors),
    )
    for t, e in errors.items():
        logger.warning("  %s: %s", t, e)

    return adj_close, errors, latest_close


def validate_data(prices: pd.DataFrame, min_rows: int = 20) -> list[str]:
    """Check data quality and return warnings."""
    warnings = []
    for t in prices.columns:
        series = prices[t].dropna()
        if len(series) < min_rows:
            warnings.append(f"{t}: only {len(series)} valid rows (min {min_rows})")
        if len(series) > 0:
            last_date = series.index[-1]
            days_stale = (pd.Timestamp.now(tz=last_date.tz) - last_date).days
            if days_stale > 5:
                warnings.append(f"{t}: last data is {last_date.date()} ({days_stale} days ago)")
    return warnings
