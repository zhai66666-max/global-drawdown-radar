"""宏观指标兜底数据源（不依赖 yfinance）。

原 nasdaq100-daily-report 的 6 个宏观指标全部走 yfinance 的 ticker：
    QQQ / ^VIX / ^TNX / DX-Y.NYB / CNY=X / SOXX
Yahoo 一限流，宏观仪表盘就整块空掉。这里按标的补上国内可用的公开行情接口：

  腾讯财经 qt.gtimg.cn   支持美股与部分指数：usQQQ / usSOXX / usVIX / usNDX
  东方财富 push2         支持国际指数与外汇：100.UDI（美元指数）、133.USDCNH（美元兑离岸人民币）

无法兜底的：
  ^TNX（10 年期美债收益率）—— 腾讯与东财的公开接口均不提供（实测）。
  该指标在 Yahoo 不可用时会自然缺席（上游用 `if v` 过滤），
  只少一张卡片，不影响其余内容与发信。

注意：这类实时行情接口**没有历史序列**，因此拿不到「历史最高价」，
`drawdown_pct` 返回 None（模板显示为「—」），不会伪造成 0 误导判断。
"""
from __future__ import annotations

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

HEADERS_TX = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
HEADERS_EM = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

# yfinance ticker → 腾讯代码
TENCENT_MAP = {
    "QQQ": "usQQQ",
    "SOXX": "usSOXX",
    "AAPL": "usAAPL",
    "^VIX": "usVIX",
    "^NDX": "usNDX",
    "NDX": "usNDX",
}

# yfinance ticker → (东财 secid, 价格缩放因子)
# 东财对不同品种的整数化精度不同，实测：美元指数 ÷100，离岸人民币 ÷10000
EASTMONEY_MAP = {
    "DX-Y.NYB": ("100.UDI", 100.0),
    "CNY=X": ("133.USDCNH", 10000.0),
}

SUPPORTED = set(TENCENT_MAP) | set(EASTMONEY_MAP)


def _get(url: str, headers: dict, timeout: int = 12, encoding: str = "utf-8") -> str | None:
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode(encoding, "replace")
    except Exception as exc:                            # noqa: BLE001
        logger.debug("宏观兜底请求失败 %s：%s", url, exc)
        return None


def _f(fields: list[str], i: int) -> float | None:
    try:
        v = fields[i].strip()
        return float(v) if v else None
    except (IndexError, ValueError):
        return None


def fetch_tencent_us(code: str) -> dict | None:
    """腾讯美股/指数行情。字段位：3=最新价 4=昨收 6=成交量 31=涨跌额 32=涨跌幅%"""
    text = _get(f"https://qt.gtimg.cn/q={code}", HEADERS_TX, encoding="gbk")
    if not text or '="' not in text:
        return None
    payload = text.split('="', 1)[1].rsplit('"', 1)[0]
    fields = payload.split("~")
    if len(fields) < 6:
        return None
    price = _f(fields, 3)
    if not price:
        return None
    prev = _f(fields, 4) or price
    chg_pct = _f(fields, 32)
    if chg_pct is None:
        chg_pct = ((price - prev) / prev * 100) if prev else 0.0
    return {
        "price": price,
        "prev_close": prev,
        "change_pct": chg_pct,
        "volume": _f(fields, 6),
        "quote_time": fields[30] if len(fields) > 30 else "",
        "source": "腾讯财经",
    }


def fetch_eastmoney(secid: str, scale: float) -> dict | None:
    """东方财富国际指数/外汇。f43=最新价(需按品种缩放) f60=昨收 f170=涨跌幅×100"""
    url = (f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}"
           f"&fields=f43,f57,f58,f60,f170")
    text = _get(url, HEADERS_EM)
    if not text:
        return None
    try:
        data = json.loads(text).get("data") or {}
    except json.JSONDecodeError:
        return None
    raw = data.get("f43")
    if not raw:
        return None
    price = float(raw) / scale
    prev_raw = data.get("f60")
    prev = (float(prev_raw) / scale) if prev_raw else price
    chg_pct = None
    if data.get("f170") not in (None, ""):
        try:
            chg_pct = float(data["f170"]) / 100.0
        except (TypeError, ValueError):
            chg_pct = None
    if chg_pct is None:
        chg_pct = ((price - prev) / prev * 100) if prev else 0.0
    return {
        "price": price,
        "prev_close": prev,
        "change_pct": chg_pct,
        "name_cn": data.get("f58"),
        "source": "东方财富",
    }


def fetch_macro_quote(ticker: str) -> dict | None:
    """按 ticker 依次尝试可用的兜底源。全失败返回 None。"""
    if ticker in TENCENT_MAP:
        q = fetch_tencent_us(TENCENT_MAP[ticker])
        if q:
            return q
    if ticker in EASTMONEY_MAP:
        secid, scale = EASTMONEY_MAP[ticker]
        q = fetch_eastmoney(secid, scale)
        if q:
            return q
    return None
