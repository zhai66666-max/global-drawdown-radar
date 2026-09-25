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

from src.providers.drawdown_radar.config import ETF_TICKERS

logger = logging.getLogger(__name__)

# 记录本次实际用到的数据源，供邮件页脚如实标注（不做假设性描述）
LAST_SOURCE = "Yahoo Finance"

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
    # 说明：原来这里重试耗尽后直接 raise，导致「一个数据源挂了雷达整块消失」。
    # 现在改为不抛异常，把没能拿到的标的交给下面的兜底源补齐。
    # 另外接入共享熔断器：Yahoo 已判定不可用就别再耗时间重试。
    from src.providers.common import yf_breaker

    data = None
    if yf_breaker.is_open():
        logger.warning("yfinance 已熔断，雷达直接走兜底源")
    else:
        for attempt in range(1, max_retries + 1):
            try:
                data = yf.download(
                    ticker_str,
                    period=period,
                    auto_adjust=False,  # Get both Close and Adj Close
                    progress=False,
                    group_by="ticker",
                )
                yf_breaker.record_success()
                break
            except Exception as exc:
                logger.warning("Download attempt %d/%d failed: %s", attempt, max_retries, exc)
                yf_breaker.record_failure(str(exc))
                if attempt < max_retries and not yf_breaker.is_open():
                    wait = YFINANCE_RETRY_DELAY * attempt
                    logger.info("Retrying in %d seconds...", wait)
                    time.sleep(wait)
                else:
                    logger.error("yfinance 重试 %d 次仍失败，转入兜底数据源", max_retries)
                    data = None
                    break

    # ── Parse multi-ticker DataFrame ────────────────────────────────────────
    adj_close = pd.DataFrame()
    errors = {}
    latest_close = {}

    for t in tickers:
        try:
            if data is None or data.empty:
                errors[t] = "yfinance 无返回"
                continue
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

    # ── 兜底：yfinance 没能给出的标的，改用不依赖 Yahoo 的数据源补齐 ────────
    global LAST_SOURCE
    missing = [t for t in tickers if t not in adj_close.columns]
    if missing:
        logger.warning("yfinance 缺失 %d 个标的，启用兜底源：%s", len(missing), missing)
        filled_before = len(missing)
        adj_close, latest_close = _fill_from_fallback(
            missing, adj_close, latest_close, errors)
        filled = filled_before - len([t for t in missing if t in errors])
        if not adj_close.empty and filled >= len(missing):
            LAST_SOURCE = "NASDAQ 官方接口（Yahoo 限流兜底）"
        elif filled:
            LAST_SOURCE = "Yahoo Finance + NASDAQ 官方接口（部分兜底）"
        else:
            LAST_SOURCE = "Yahoo Finance（兜底亦失败）"
    else:
        LAST_SOURCE = "Yahoo Finance 复权价"

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


def _fill_from_fallback(
    missing: list[str],
    adj_close: pd.DataFrame,
    latest_close: dict[str, float],
    errors: dict[str, str],
) -> tuple[pd.DataFrame, dict[str, float]]:
    """用 NASDAQ 官方 chart 接口补齐 yfinance 未提供的标的。

    雷达需要的是「长历史复权/收盘价序列」，兜底源给的是未复权收盘价。
    对本项目的用途（回撤幅度、波动率、距历史高点）而言，
    复权与否只影响分红率量级的偏差，不影响结论方向，因此可接受；
    这里在日志里明确标注来源，避免和 yfinance 数据混淆。
    """
    from src.providers.common import us_history

    bundles = us_history.fetch_many(missing, assetclass="etf", max_workers=5)
    for t in missing:
        b = bundles.get(t)
        if not b or not b.ok:
            errors[t] = "yfinance 与兜底源均无数据"
            continue
        series = b.df.set_index(pd.to_datetime(b.df["date"]))["close"].dropna()
        series.name = t
        if series.empty:
            errors[t] = "兜底源返回空序列"
            continue
        if adj_close.empty:
            adj_close = pd.DataFrame(series)
        else:
            adj_close = adj_close.join(series, how="outer")
        if b.last is not None:
            latest_close[t] = float(b.last)
        errors.pop(t, None)                 # 已补齐，从错误清单里移除
        logger.info("  %s ← 兜底源补齐 %d 行（%s 起）",
                    t, len(series), series.index[0].date())
    return adj_close, latest_close


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
