#!/usr/bin/env python3
"""
纳斯达克100 QDII 每日深度分析报告
每天早上 6:00（北京时间）自动抓取多维度数据、评分、DeepSeek AI 分析，
生成中文 HTML 邮件并发送。GitHub Actions 定时任务。
"""

import os
import sys
import json
import html
import smtplib
import logging
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yfinance as yf
import requests

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ─── 配置 ─────────────────────────────────────────────────────────────────────

DEEPSEEK_API = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"  # deepseek-chat 已于 2026-07-24 废弃

TOP_COMPONENTS = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "AVGO",
    "COST", "PEP", "ADBE", "CSCO", "CMCSA", "TXN", "QCOM", "AMD",
    "AMGN", "INTU", "AMAT", "ISRG", "REGN", "VRTX", "PANW", "MU",
    "LRCX", "KLAC", "ADP", "SNPS", "CDNS", "MELI", "CTAS", "CRWD",
    "MAR", "ORLY", "MRVL", "WDAY", "DASH", "ABNB", "FTNT", "ADSK",
]

# 公司与中文短名对照。
# 存在的意义：兜底数据源（NASDAQ 官方接口）只给代码不给公司名，
# 若完全依赖 yfinance 的 shortName，一旦走兜底邮件里就全是重复的代码。
COMPANY_NAMES = {
    "AAPL": "苹果", "MSFT": "微软", "AMZN": "亚马逊", "NVDA": "英伟达",
    "GOOGL": "谷歌", "META": "Meta", "TSLA": "特斯拉", "AVGO": "博通",
    "COST": "好市多", "PEP": "百事", "ADBE": "Adobe", "CSCO": "思科",
    "CMCSA": "康卡斯特", "TXN": "德州仪器", "QCOM": "高通", "AMD": "超威半导",
    "AMGN": "安进", "INTU": "财捷", "AMAT": "应用材料", "ISRG": "直觉外科",
    "REGN": "再生元", "VRTX": "福泰制药", "PANW": "派拓网络", "MU": "美光",
    "LRCX": "泛林集团", "KLAC": "科天半导体", "ADP": "自动数据处理", "SNPS": "新思科技",
    "CDNS": "楷登电子", "MELI": "美客多", "CTAS": "信达思", "CRWD": "CrowdStrike",
    "MAR": "万豪", "ORLY": "奥莱利", "MRVL": "迈威尔", "WDAY": "Workday",
    "DASH": "DoorDash", "ABNB": "爱彼迎", "FTNT": "飞塔", "ADSK": "欧特克",
}

# 本次成分股实际用到的数据源（页脚如实标注用）
LAST_COMPONENTS_SOURCE = "Yahoo Finance"


def _breaker():
    """yfinance 熔断器（共享模块，见 providers/common/yf_breaker.py）。"""
    from src.providers.common import yf_breaker
    return yf_breaker

# 宏观指标配置：(ticker, 名称, 满分, 偏好多头方向)
# 偏好方向: "lower" 表示值越低得分越高, "higher" 表示值越高得分越高
MACRO_INDICATORS = [
    ("QQQ", "QQQ 价格", 10, "higher"),
    ("^VIX", "VIX 恐慌指数", 15, "lower"),
    ("^TNX", "10Y 国债收益率", 15, "lower"),
    ("DX-Y.NYB", "DXY 美元指数", 10, "lower"),
    ("CNY=X", "USD/CNY 汇率", 15, "lower"),
    ("SOXX", "SOXX 半导体", 10, "higher"),
]


# ─── 数据获取 ─────────────────────────────────────────────────────────────────

def fetch_ticker_data(ticker: str, period: str = "1y") -> Optional[Dict[str, Any]]:
    """获取单个 ticker 的最新数据。yfinance 失败或已熔断时走兜底源。"""
    if _breaker().is_open():
        return _fetch_ticker_fallback(ticker, period)
    try:
        t = yf.Ticker(ticker)
        # QQQ 用全量历史数据，计算历史最高收盘价回撤
        if ticker == "QQQ":
            period = "max"
        hist = t.history(period=period)
        # 去掉 NaN 行（yfinance 1.5.x 周末追加的占位行）
        if not hist.empty:
            hist = hist.dropna(subset=["Close"])
        if hist.empty:
            _breaker().record_failure(f"{ticker} 无数据")
            return _fetch_ticker_fallback(ticker, period)
        cur = _safe_float(hist["Close"].iloc[-1])
        if cur == 0.0:
            _breaker().record_failure(f"{ticker} 价格为 0")
            return _fetch_ticker_fallback(ticker, period)
        _breaker().record_success()
        all_time_high = _safe_float(hist["Close"].max())
        if all_time_high == 0.0:
            all_time_high = cur
        # 最大回撤（从历史最高收盘价）
        drawdown = (cur - all_time_high) / all_time_high * 100
        prev_val = _safe_float(hist["Close"].iloc[-2]) if len(hist) >= 2 else cur
        change_pct = ((cur - prev_val) / prev_val * 100) if prev_val != 0.0 else 0.0
        return {
            "ticker": ticker,
            "price": round(cur, 4),
            "all_time_high": round(all_time_high, 4),
            "drawdown_pct": round(drawdown, 2),
            "change_pct": round(change_pct, 2),
        }
    except Exception as exc:
        logger.warning("获取 %s 失败: %s", ticker, exc)
        _breaker().record_failure(str(exc))
        return _fetch_ticker_fallback(ticker, period)


# 兜底源能覆盖的标的（全部为美股上市证券）；VIX/TNX/DXY/汇率不在其中
_FALLBACK_TICKERS = {"QQQ": "etf", "SOXX": "etf", "^NDX": "index", "NDX": "index"}


def _fetch_ticker_fallback(ticker: str, period: str) -> Optional[Dict[str, Any]]:
    """yfinance 不可用时的兜底，两级：

    ① NASDAQ 官方 chart —— 有全历史，能算出「历史最高价」与回撤（首选）
    ② 腾讯财经 / 东方财富实时行情 —— 只有最新价，没有历史，
       因此 drawdown_pct 返回 None（模板显示「—」），而不伪造成 0。

    仍拿不到的：^TNX（10 年期美债收益率），公开免费接口均不提供，
    该指标本轮缺席，只少一张卡片。
    """
    # ── ① NASDAQ 官方 chart（仅美股上市证券）
    ac = _FALLBACK_TICKERS.get(ticker)
    if ac:
        try:
            from src.providers.common import us_history
            b = us_history.fetch_bundle(ticker.lstrip("^"), ac)
            if b and b.ok:
                closes = b.df["close"]
                cur = float(closes.iloc[-1])
                # period="max" 才是真·历史最高；否则取窗口内最高，与原 yfinance 语义一致
                ath = float(closes.max())
                prev_val = float(closes.iloc[-2]) if len(closes) >= 2 else cur
                change_pct = ((cur - prev_val) / prev_val * 100) if prev_val else 0.0
                drawdown = (cur - ath) / ath * 100 if ath else 0.0
                logger.info("%s ← 兜底源① NASDAQ chart（%d 行）", ticker, len(closes))
                return {
                    "ticker": ticker,
                    "price": round(cur, 4),
                    "all_time_high": round(ath, 4),
                    "drawdown_pct": round(drawdown, 2),
                    "change_pct": round(change_pct, 2),
                }
        except Exception as exc:                        # noqa: BLE001
            logger.warning("%s NASDAQ 兜底失败: %s", ticker, exc)

    # ── ② 腾讯 / 东财实时行情
    try:
        from src.providers.common import macro_fallback
        q = macro_fallback.fetch_macro_quote(ticker)
        if q and q.get("price"):
            logger.info("%s ← 兜底源② %s（无历史，回撤记 —）", ticker, q.get("source"))
            return {
                "ticker": ticker,
                "price": round(float(q["price"]), 4),
                "all_time_high": None,          # 无历史序列，不编造
                "drawdown_pct": None,           # 模板显示为「—」
                "change_pct": round(float(q.get("change_pct") or 0.0), 2),
                "price_source": q.get("source"),
            }
    except Exception as exc:                            # noqa: BLE001
        logger.warning("%s 行情兜底失败: %s", ticker, exc)

    logger.warning("%s 无可用兜底源（非美股上市证券且无公开行情），本轮跳过", ticker)
    return None


def fetch_all_macro_indicators() -> Dict[str, Dict[str, Any]]:
    """并发获取所有宏观指标。"""
    results: Dict[str, Dict[str, Any]] = {}

    def _fetch_one(cfg: tuple) -> tuple:
        ticker, name, max_score, direction = cfg
        data = fetch_ticker_data(ticker)
        return (name, data, max_score, direction)

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_fetch_one, cfg): cfg for cfg in MACRO_INDICATORS}
        for fut in as_completed(futures):
            name, data, max_score, direction = fut.result()
            if data:
                data["name"] = name
                data["max_score"] = max_score
                data["direction"] = direction
                results[name] = data
    return results


def score_macro_indicator(data: Dict[str, Any]) -> Dict[str, Any]:
    """对单个宏观指标打分（0 ~ max_score）。"""
    max_s = data["max_score"]
    direction = data["direction"]

    name = data["name"]
    price = data["price"]
    # 兜底源可能给不出历史高点，drawdown_pct 为 None。
    # 打分只对 QQQ / SOXX 分支用到它，此处把 None 当 0 参与计算；
    # 但**输出时保留 None**，否则模板会把「无历史序列」画成「回撤 +0.00%」。
    dd_raw = data.get("drawdown_pct")
    dd = dd_raw if dd_raw is not None else 0

    if "QQQ" in name:
        # QQQ: 回撤越小越好，0% 回撤 = 满分，-10% 回撤 = 0 分
        score = max(0, min(max_s, round((1 + dd / 10) * max_s))) if dd <= 0 else max(0, round((1 - dd / 10) * max_s))
    elif "VIX" in name:
        # VIX: <15 = 满分, >35 = 0 分
        score = max(0, min(max_s, round((35 - price) / 20 * max_s)))
    elif "10Y" in name or "国债" in name:
        # 10Y: <3.5% = 满分, >6% = 0 分
        score = max(0, min(max_s, round((6.0 - price) / 2.5 * max_s)))
    elif "DXY" in name or "美元" in name:
        # DXY: <95 = 满分, >110 = 0 分
        score = max(0, min(max_s, round((110 - price) / 15 * max_s)))
    elif "CNY" in name or "汇率" in name:
        # USD/CNY: <6.5 = 满分, >7.5 = 0 分
        score = max(0, min(max_s, round((7.5 - price) / 1.0 * max_s)))
    elif "SOXX" in name or "半导体" in name:
        # SOXX: 回撤越小越好
        score = max(0, min(max_s, round((1 + dd / 15) * max_s))) if dd <= 0 else max(0, round((1 - dd / 15) * max_s))
    else:
        score = max_s // 2  # 默认半分数

    return {
        "name": name,
        "price": price,
        "drawdown_pct": dd_raw,
        "score": score,
        "max_score": max_s,
        "score_pct": round(score / max_s * 100) if max_s else 0,
        "direction": direction,
    }


def _safe_float(val: Any) -> float:
    """安全转换为 float，NaN 返回 0.0。"""
    try:
        f = float(val)
        return f if not np.isnan(f) else 0.0
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val: Any) -> int:
    """安全转换为 int，NaN 返回 0。"""
    try:
        return int(float(val)) if not np.isnan(float(val)) else 0
    except (ValueError, TypeError):
        return 0


def _bar_phase(last_bar: str) -> str:
    """最后一根日线属于「盘中」还是「已收盘」。

    用美东时间判定：yfinance 在开盘后才生成当天的 bar，
    所以 last_bar == 美东今天 且 未过 16:00 → 盘中；否则视为收盘价。
    判不出来时返回空串，调用方退回只显示日期，不会影响渲染。
    """
    if not last_bar:
        return ""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:                                   # noqa: BLE001
        return ""
    if last_bar != now_et.strftime("%Y-%m-%d"):
        return "收盘"
    return "盘中" if now_et.hour < 16 else "今日收盘"


def _load_index_history(ticker: str, label: str, rows: int = 252):
    """取指数近一年日线。返回 (hist, info)，两者都可能为空。

    数据源顺序（原项目只有第一级，Yahoo 一限流整块内容就没了）：
      1. yfinance
      2. NASDAQ 官方 historical 接口 —— 仅 ^NDX，含完整 OHLCV（首选兜底）
      3. NASDAQ 官方 chart 接口 —— 覆盖 ETF 与个股（QQQ 走这条）
    """
    # ① yfinance（已熔断则直接跳过，省掉几分钟无谓等待）
    if not _breaker().is_open():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="1y")
            if hist is not None and not hist.empty:
                hist = hist.dropna(subset=["Close"])
            if hist is not None and not hist.empty:
                _breaker().record_success()
                return hist, (t.info or {}), "Yahoo Finance"
            logger.warning("%s (%s) yfinance 无数据，转兜底源", label, ticker)
            _breaker().record_failure(f"{ticker} 无数据")
        except Exception as exc:                        # noqa: BLE001
            logger.warning("%s (%s) yfinance 失败：%s，转兜底源", label, ticker, exc)
            _breaker().record_failure(str(exc))

    sym = ticker.lstrip("^")

    # ② NASDAQ 官方 historical：NDX 全历史 OHLCV（复用 etf_monitor 已验证的降级链）
    if sym in ("NDX", "QQQ"):
        try:
            from src.providers.etf_monitor.data_fetcher import fetch_nasdaq_history
            df, src_name = fetch_nasdaq_history()       # 该函数只抓 NDX
            if df is not None and not df.empty:
                df = df.drop_duplicates(subset="date").sort_values("date")
                hist = df.tail(rows).reset_index(drop=True)
                hist.columns = [str(c).capitalize() for c in hist.columns]
                hist = hist.set_index("Date")
                logger.info("%s 兜底命中：%s（%d 行）", label, src_name, len(hist))
                return hist, {}, "NASDAQ 官方接口"
        except Exception as exc:                        # noqa: BLE001
            logger.warning("%s NDX 兜底失败：%s", label, exc)

    # ③ NASDAQ 官方 chart
    try:
        from src.providers.common import us_history
        b = us_history.fetch_bundle(sym, "index" if sym == "NDX" else "etf")
        if b and b.ok:
            hist = b.df.tail(rows).reset_index(drop=True)
            hist.columns = [str(c).capitalize() for c in hist.columns]
            hist = hist.set_index("Date")
            logger.info("%s 兜底命中：NASDAQ chart（%d 行）", label, len(hist))
            return hist, {"previousClose": b.prev_close,
                          "shortName": b.symbol}, "NASDAQ 官方接口"
    except Exception as exc:                            # noqa: BLE001
        logger.warning("%s chart 兜底失败：%s", label, exc)

    return None, {}, ""


def _fetch_index_data(ticker: str, label: str) -> Dict[str, Any]:
    """获取指数数据（先尝试指定 ticker，失败则回退到 QQQ）。"""
    hist, info, src = _load_index_history(ticker, label)

    # 如果数据取不到，回退到 QQQ
    if hist is None or hist.empty:
        if ticker != "QQQ":
            logger.warning("%s (%s) 数据为空，回退到 QQQ", label, ticker)
            return _fetch_index_data("QQQ", "QQQ (纳斯达克100 ETF)")
        raise ValueError(f"无法获取 {label} 历史数据（包括 QQQ 回退也失败）")

    if hist["Close"].isna().all():
        raise ValueError(f"{label} 数据全为 NaN")

    cur = _safe_float(hist["Close"].iloc[-1])
    prev_close = _safe_float(info.get("previousClose", hist["Close"].iloc[-2] if len(hist) > 1 else cur))
    if prev_close == 0.0:
        prev_close = cur
    change = cur - prev_close
    change_pct = (change / prev_close) * 100 if prev_close else 0.0

    high_52w = _safe_float(hist["High"].max())
    low_52w = _safe_float(hist["Low"].min())
    day_low = _safe_float(hist["Low"].iloc[-1])
    day_high = _safe_float(hist["High"].iloc[-1])
    volume = _safe_int(hist["Volume"].iloc[-1])

    # MA-50
    ma_50_val = hist["Close"].rolling(window=50).mean().iloc[-1]
    ma_50 = round(float(ma_50_val), 2) if not pd.isna(ma_50_val) else None

    # MA-200
    ma_200 = None
    if len(hist) >= 200:
        ma_200_val = hist["Close"].rolling(window=200).mean().iloc[-1]
        ma_200 = round(float(ma_200_val), 2) if not pd.isna(ma_200_val) else None

    # RSI
    delta = hist["Close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)  # 避免除零
    rsi_series = 100 - (100 / (1 + rs))
    rsi_val = float(rsi_series.iloc[-1])
    rsi = round(rsi_val, 2) if not np.isnan(rsi_val) else None

    # 20日均量
    avg_vol_20 = None
    if len(hist) >= 20:
        v20 = hist["Volume"].rolling(window=20).mean().iloc[-1]
        avg_vol_20 = round(float(v20)) if not pd.isna(v20) else None

    # 当日开盘（原来再打一次 yfinance 取 5 日线；现在直接用已加载的日线末行）
    _open = hist["Open"].iloc[-1] if "Open" in hist.columns else None
    open_p = round(_safe_float(_open), 2) if _open is not None and not pd.isna(_open) else None

    symbol_name = "纳斯达克100" if ticker == "^NDX" else "QQQ (纳斯达克100 ETF)"

    # 这根日线是哪一天的、算不算「已收盘」——页头要显示出来。
    # 关键点：盘中时 yfinance 会把当天那根还没走完的 bar 当作最后一行，
    # 所以页头的数字是实时价；而回撤那一侧（etf_monitor）用的是 NASDAQ
    # 官方历史接口，要等收盘后才收录当天 → 两边会差一个交易日。
    try:
        last_bar = str(hist.index[-1])[:10]
    except Exception:                                   # noqa: BLE001
        last_bar = ""
    phase = _bar_phase(last_bar)
    bar_label = f"{last_bar[5:]} {phase}".strip() if last_bar else ""

    return {
        "symbol": ticker, "name": symbol_name,
        "current_price": round(cur, 2), "prev_close": round(prev_close, 2),
        "change": round(change, 2), "change_pct": round(change_pct, 2),
        "open": open_p, "day_high": round(day_high, 2), "day_low": round(day_low, 2),
        "high_52w": round(high_52w, 2), "low_52w": round(low_52w, 2),
        "volume": volume, "avg_vol_20": avg_vol_20,
        "ma_50": ma_50, "ma_200": ma_200, "rsi": rsi,
        "last_bar": last_bar, "bar_phase": phase, "bar_label": bar_label,
        "data_source": src or "未知",
        "date": datetime.now().strftime("%Y-%m-%d"),
    }


def fetch_nasdaq100_data() -> Dict[str, Any]:
    """获取纳斯达克100指数数据及技术指标（^NDX 不可用时自动回退到 QQQ）。"""
    logger.info("正在获取纳斯达克100指数数据 …")
    return _fetch_index_data("^NDX", "纳斯达克100")


def fetch_top_components() -> List[Dict[str, Any]]:
    """获取成分股表现。yfinance 拿不到的个股改用 NASDAQ 官方接口补齐。"""
    global LAST_COMPONENTS_SOURCE
    logger.info("正在获取 %d 只成分股数据 …", len(TOP_COMPONENTS))
    data: List[Dict[str, Any]] = []
    missed: List[str] = []
    yf_ok = 0

    for sym in TOP_COMPONENTS:
        if missed and _breaker().is_open():
            # yfinance 已熔断，剩下的标的直接交给兜底，不再逐个白等
            missed.append(sym)
            continue
        try:
            tkr = yf.Ticker(sym)
            hist = tkr.history(period="5d")
            if hist.empty or len(hist) < 2:
                missed.append(sym)
                _breaker().record_failure(f"{sym} 无数据")
                continue
            _breaker().record_success()
            info = tkr.info or {}
            cur = float(hist["Close"].iloc[-1])
            prv = float(hist["Close"].iloc[-2])
            chg_pct = (cur - prv) / prv * 100 if prv else 0.0
            vol = int(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else 0
            data.append({
                "symbol": sym,
                "name": info.get("shortName") or COMPANY_NAMES.get(sym, sym),
                "price": round(cur, 2), "change": round(cur - prv, 2),
                "change_pct": round(chg_pct, 2), "volume": vol,
                "market_cap": info.get("marketCap"),
            })
            yf_ok += 1
        except Exception as exc:
            logger.warning("跳过 %s: %s", sym, exc)
            missed.append(sym)
            _breaker().record_failure(str(exc))

    # ── 兜底：NASDAQ 官方 chart（一次请求含全历史 + 实时报价）
    if missed:
        logger.warning("yfinance 缺失 %d 只成分股，启用兜底源：%s", len(missed), missed)
        try:
            from src.providers.common import us_history
            bundles = us_history.fetch_many(missed, assetclass="stocks", max_workers=5)
            for sym in missed:
                b = bundles.get(sym)
                if not b or not b.ok:
                    continue
                cur = float(b.last if b.last is not None else b.df["close"].iloc[-1])
                prv = float(b.prev_close if b.prev_close is not None
                            else b.df["close"].iloc[-2])
                chg_pct = (cur - prv) / prv * 100 if prv else 0.0
                vol = int(b.volume or (b.df["volume"].iloc[-1] if len(b.df) else 0))
                data.append({
                    "symbol": sym, "name": COMPANY_NAMES.get(sym, sym),
                    "price": round(cur, 2), "change": round(cur - prv, 2),
                    "change_pct": round(chg_pct, 2), "volume": vol,
                    "market_cap": None,
                })
        except Exception as exc:                        # noqa: BLE001
            logger.warning("成分股兜底源失败：%s", exc)

    if yf_ok == len(TOP_COMPONENTS):
        LAST_COMPONENTS_SOURCE = "Yahoo Finance"
    elif yf_ok:
        LAST_COMPONENTS_SOURCE = "Yahoo Finance + NASDAQ 官方接口"
    else:
        LAST_COMPONENTS_SOURCE = "NASDAQ 官方接口"

    logger.info("成分股最终取得 %d/%d 只（来源：%s）",
                len(data), len(TOP_COMPONENTS), LAST_COMPONENTS_SOURCE)
    data.sort(key=lambda x: x["change_pct"], reverse=True)
    return data


# ─── 工具 ─────────────────────────────────────────────────────────────────────

def fmt_vol(v: int) -> str:
    if v >= 1_000_000_000: return f"{v/1_000_000_000:.2f}亿"
    if v >= 1_000_000: return f"{v/1_000_000:.2f}百万"
    if v >= 1_000: return f"{v/1_000:.2f}千"
    return str(v)


def _badge(condition: bool, t: str, f: str) -> str:
    if condition:
        return '<span style="font-size:12px;font-weight:600;padding:4px 10px;border-radius:10px;background-color:rgba(34,197,94,0.15);color:#22c55e;">%s</span>' % t
    return '<span style="font-size:12px;font-weight:600;padding:4px 10px;border-radius:10px;background-color:rgba(239,68,68,0.15);color:#ef4444;">%s</span>' % f


def _comment_row(label: str, text: str, color: str = "#f59e0b") -> str:
    return (
        f'<tr><td style="padding:8px 16px;font-size:13px;color:{color};vertical-align:top;width:24px;">▶</td>'
        f'<td style="padding:8px 0;font-size:13px;"><span style="color:#f1f5f9;font-weight:600;">{label}</span>'
        f'<span style="color:#94a3b8;"> — {text}</span></td></tr>'
    )


def score_color(score_pct: int) -> str:
    """根据得分百分比返回颜色。"""
    if score_pct >= 70: return "#22c55e"
    if score_pct >= 40: return "#f59e0b"
    return "#ef4444"


def score_label(score_pct: int) -> str:
    if score_pct >= 70: return "良好"
    if score_pct >= 40: return "中性"
    return "警惕"


# ─── DeepSeek 深度分析 ────────────────────────────────────────────────────────

def deepseek_analysis(
    ix: Dict[str, Any],
    macro_scores: List[Dict[str, Any]],
    components: List[Dict[str, Any]],
    api_key: str,
) -> Optional[str]:
    """调用 DeepSeek 生成 QDII 投资者视角的深度分析报告。"""

    # 构建数据摘要
    total_score = sum(m["score"] for m in macro_scores)
    max_possible = sum(m["max_score"] for m in macro_scores)
    score_ratio = round(total_score / max_possible * 100) if max_possible else 0

    macro_lines = []
    for m in macro_scores:
        macro_lines.append(
            f"- {m['name']}: {m['price']} | 回撤 {m.get('drawdown_pct', 0):.2f}% | "
            f"得分 {m['score']}/{m['max_score']} ({m['score_pct']}%) [{score_label(m['score_pct'])}]"
        )

    up_count = sum(1 for c in components if c["change_pct"] > 0)
    dn_count = sum(1 for c in components if c["change_pct"] < 0)

    # QQQ 回撤加仓策略评估
    qqq_dd = 0.0
    qqq_buy_signal = "正常定投（回撤 < 10%）"
    for m in macro_scores:
        if "QQQ" in m.get("name", ""):
            qqq_dd = abs(m.get("drawdown_pct", 0))
            if qqq_dd > 20:
                qqq_buy_signal = f"🔴 五倍加仓触发！QQQ 回撤 {qqq_dd:.2f}%，已突破 20% 阈值，应 5x 大额投入"
            elif qqq_dd > 10:
                qqq_buy_signal = f"🟡 双倍加仓触发！QQQ 回撤 {qqq_dd:.2f}%，已突破 10% 阈值，应 2x 投入"
            break

    prompt = f"""你是纳斯达克100 QDII 投资分析专家。请基于以下数据，为持有纳斯达克100 QDII 基金的中国投资者撰写一份深度分析报告。

## 上一交易日数据

**纳斯达克100 指数:**
- 收盘价: {ix['current_price']:,.2f}
- 涨跌: {ix['change']:+,.2f} ({ix['change_pct']:+.2f}%)
- 开盘: {ix['open']:,.2f} | 前收盘: {ix['prev_close']:,.2f}
- 日内区间: {ix['day_low']:,.2f} – {ix['day_high']:,.2f}
- 52周区间: {ix['low_52w']:,.2f} – {ix['high_52w']:,.2f}
- 52周分位: {(ix['current_price'] - ix['low_52w']) / (ix['high_52w'] - ix['low_52w']) * 100:.1f}%
- 成交量: {ix['volume']:,}
- 50日均线: {ix.get('ma_50', 'N/A')} | 200日均线: {ix.get('ma_200', 'N/A')}
- RSI(14): {ix.get('rsi', 'N/A')}

**宏观指标评分（总分 {total_score}/{max_possible} = {score_ratio}%）:**
{chr(10).join(macro_lines)}

**成分股统计:** {up_count} 涨 / {dn_count} 跌 / {len(components)} 只

**QQQ 回撤加仓策略状态:** {qqq_buy_signal}（规则：回撤 >10% 双倍投入，>20% 五倍投入）

请按以下格式输出（每个部分必须包含实质性分析，不要泛泛而谈）：

【市场总览】
用 2-3 句话概括当日纳斯达克100的整体表现和关键驱动因素。

【宏观环境评估】
结合 VIX、10Y 美债收益率、DXY 美元指数、USD/CNY 汇率等指标，评估当前宏观环境对纳斯达克100 QDII 投资的有利/不利因素。特别关注汇率变化对中国投资者的影响。

【技术面分析】
分析均线系统（50日/200日均线）、RSI 动能、52周分位、成交量等技术信号。指出关键技术位和潜在支撑/阻力。

【加仓策略评估】
基于 QQQ 回撤数据，评估当前是否触发加仓信号（>10% 双倍、>20% 五倍）。结合技术面和宏观面，判断当前是否是加仓的好时机，给出具体操作建议和仓位规划。

【风险提示】
列出当前最需要关注的 2-3 个风险点（如估值过高、政策变化、流动性、地缘政治等）。

注意：用专业但通俗的中文，直接给出判断和数据支撑，不要模棱两可。每个部分 3-5 句话。"""

    try:
        resp = requests.post(
            DEEPSEEK_API,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": "你是资深QDII投资分析师，擅长结合宏观、技术、政策多维分析，给出专业且实用的投资建议。请严格按照格式输出，每部分都要有具体数据支撑。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.4,
                "max_tokens": 1500,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("DeepSeek 分析失败: %s", exc)
        return None


