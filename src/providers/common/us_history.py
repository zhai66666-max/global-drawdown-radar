"""美股日线兜底数据源（不依赖 yfinance）。

背景：被合并进来的三个旧项目，数据源成熟度差别很大——
  · nasdaq-etf-monitor   已有「NASDAQ官方API → yfinance → 本地CSV」三级降级
  · global-drawdown-radar 只有 yfinance 单源
  · nasdaq100-daily-report 只有 yfinance 单源
后果是后两个一旦被 Yahoo 限流（YFRateLimitError），整块内容就丢了。

本模块补上缺口：用 NASDAQ 官方 chart 接口，
**一次请求**即可拿到标的自上市以来的全部日线（含成交量），并附带实时报价字段。
实测覆盖范围：
  ✅ 全部美股 ETF   QQQ / SPY / GLD / EWJ / EWY / INDA / EWT / EWC / EWW / EWA / EWZ / QQQM / SOXX …
  ✅ 全部美股个股   AAPL / NVDA / …（40 只成分股均可）
  ✅ 指数           NDX（assetclass=index）
  ❌ VIX / TNX / DXY / CNY=X —— 这些不是美股上市证券，接口不提供。
     保持原有 yfinance 单源即可：拿不到就自动跳过该项指标，不影响其余内容。

对外只暴露两个函数：
    fetch_bundle(symbol, assetclass=None)        → ChartBundle | None
    fetch_many(symbols, assetclass, ...)         → dict[symbol, ChartBundle]
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

CHART_URL = ("https://api.nasdaq.com/api/quote/{sym}/chart"
             "?assetclass={ac}&fromdate=1990-01-01&todate={today}")
HISTORY_START = "1990-01-01"
# assetclass 猜测顺序：先当 ETF（雷达与多数标的都是 ETF），再当个股，最后当指数
ASSET_CLASS_CANDIDATES = ("etf", "stocks", "index")
_EAC = {"etf": ("etf", "stocks", "index"),
        "stocks": ("stocks", "etf", "index"),
        "index": ("index", "etf", "stocks")}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nasdaq.com/",
}


@dataclass
class ChartBundle:
    """单个标的的全历史日线 + 实时报价快照。"""
    symbol: str
    df: pd.DataFrame                     # date/open/high/low/close/volume，按日期升序
    last: float | None = None            # 最新价（盘中实时）
    prev_close: float | None = None
    change: float | None = None
    change_pct: float | None = None      # 百分数，如 +0.64
    volume: float | None = None
    assetclass: str = ""
    source: str = "nasdaq_chart"

    @property
    def ok(self) -> bool:
        return self.df is not None and not self.df.empty


def _to_float(v, strip: bool = True) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v == v else None          # 过滤 NaN
    s = str(v).strip()
    if strip:
        s = s.replace(",", "").replace("$", "").replace("%", "").replace("+", "")
    if not s or s in {"--", "N/A", "n/a"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _get_json(url: str, timeout: int = 30, retries: int = 2):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status != 200:
                    raise urllib.error.HTTPError(url, r.status, "", r.headers, None)
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as exc:                     # noqa: BLE001
            if attempt >= retries:
                logger.debug("chart 请求失败 %s：%s", url, exc)
                return None
            time.sleep(1.2 * (attempt + 1))
    return None


def _parse_chart(sym: str, payload: dict, assetclass: str) -> ChartBundle | None:
    data = (payload or {}).get("data") or {}
    chart = data.get("chart") or []
    if not chart:
        return None

    rows = []
    for pt in chart:
        z = pt.get("z") or {}
        # 字段名不统一：ETF/个股用 close，指数（如 NDX）用 lastSalePrice，
        # 部分点位只有顶层 y（数值形式的收盘价）。三者依次兜。
        close = (_to_float(z.get("close"))
                 or _to_float(z.get("value"))
                 or _to_float(z.get("lastSalePrice"))
                 or _to_float(pt.get("y")))
        if close is None:
            continue                              # 早期点位可能缺值，跳过而不是报错
        dt = z.get("dateTime")
        if not dt:
            continue
        rows.append({
            "date": dt,
            "open": _to_float(z.get("open")) or close,
            "high": _to_float(z.get("high")) or close,
            "low": _to_float(z.get("low")) or close,
            "close": close,
            "volume": _to_float(z.get("volume")) or 0.0,
        })
    if not rows:
        return None

    df = pd.DataFrame(rows)
    # 日期形如 9/24/2026；先按 pandas 解析再统一格式，避免 locale 差异
    df["date"] = pd.to_datetime(df["date"], format="%m/%d/%Y", errors="coerce")
    df = (df.dropna(subset=["date"])
            .drop_duplicates(subset="date")
            .sort_values("date")
            .reset_index(drop=True))
    if df.empty:
        return None
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    # 早期点位成交量常为 0，转成可空整数
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")

    last = _to_float(data.get("lastSalePrice"))
    prev = _to_float(data.get("previousClose"))
    chg_pct = _to_float(data.get("percentageChange"))
    chg = _to_float(data.get("netChange"), strip=False)
    if last is None:
        last = float(df["close"].iloc[-1])
    if prev is None:
        prev = float(df["close"].iloc[-2]) if len(df) >= 2 else last
    if chg is None:
        chg = last - prev
    if chg_pct is None:
        chg_pct = (chg / prev * 100) if prev else 0.0

    return ChartBundle(
        symbol=sym, df=df, last=last, prev_close=prev,
        change=chg, change_pct=chg_pct,
        volume=_to_float(data.get("volume"), strip=False),
        assetclass=assetclass,
    )


def fetch_bundle(symbol: str, assetclass: str | None = None,
                 timeout: int = 30) -> ChartBundle | None:
    """取单个标的的全历史日线 + 报价。assetclass 不传则自动试 etf→stocks→index。"""
    from datetime import date
    today = date.today().isoformat()
    for ac in (_EAC.get(assetclass or "", ASSET_CLASS_CANDIDATES)):
        payload = _get_json(CHART_URL.format(sym=symbol, ac=ac, today=today),
                            timeout=timeout)
        if not payload:
            continue
        bundle = _parse_chart(symbol, payload, ac)
        if bundle and bundle.ok:
            return bundle
    return None


def fetch_many(symbols: list[str], assetclass: str | None = None,
               max_workers: int = 6, timeout: int = 30,
               pause: float = 0.0, retry_pass: bool = True) -> dict[str, ChartBundle]:
    """并发取多个标的。单个失败只记日志，不影响其余。

    第一轮并发跑完后，若还有缺的，会**降并发再补一轮**。
    NASDAQ 接口在高并发下会随机丢几个请求，串行补一遍基本都能拿回来；
    不补的话邮件里就会莫名其妙少几只票。
    """
    out: dict[str, ChartBundle] = {}
    if not symbols:
        return out

    def _round(targets: list[str], workers: int, label: str) -> list[str]:
        still: list[str] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(fetch_bundle, s, assetclass, timeout): s for s in targets}
            for fut in as_completed(futs):
                s = futs[fut]
                try:
                    b = fut.result()
                except Exception as exc:             # noqa: BLE001
                    logger.debug("兜底源取 %s 异常：%s", s, exc)
                    b = None
                if b and b.ok:
                    out[s] = b
                else:
                    still.append(s)
                if pause:
                    time.sleep(pause)
        if still:
            logger.info("兜底源第%s轮：成功 %d/%d，待补 %s",
                        label, len(targets) - len(still), len(targets), still)
        return still

    rest = _round(symbols, max_workers, "一")
    if retry_pass and rest:
        rest = _round(rest, max(2, max_workers // 3), "二")
        if rest:
            rest = _round(rest, 1, "三")       # 最后串行兜一次

    logger.info("兜底源（NASDAQ chart）：成功 %d/%d", len(out), len(symbols))
    return out


def to_close_frame(bundles: dict[str, ChartBundle]) -> pd.DataFrame:
    """合并成「日期 × 标的」的收盘价宽表（雷达模块需要这个形状）。"""
    series = {}
    for sym, b in bundles.items():
        if b.ok:
            s = b.df.set_index("date")["close"]
            s.index = pd.to_datetime(s.index)
            series[sym] = s
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).sort_index()
