"""判断「最后一根日线到底算不算已收盘」—— 全项目只留这一份实现。

为什么会需要它
--------------------------------------------------------------------------
yfinance 在开盘后就生成当天的 bar，所以**美股盘中**时，任何日线序列的最后一行
都是「当天还没走完」的那根；官方兜底源（NASDAQ historical / chart）反而要等
收盘后才收录当天。两者混在一起就会出现同一封邮件里两个数字互相矛盾。

两处都要做这个判断：
  · nasdaq100     —— 页头与 04 的盘面快照走实时；52 周区间与分位、均线、RSI、
                     20 日均量走收盘口径
  · drawdown_radar —— 09 区块的价格/涨跌走实时；10/11 的回撤、分位走收盘口径

**各写一份必然会走散**（52 周分位就是这么分叉的：一处用实时价、一处用收盘价，
表现为「指数变了但回撤没动」），所以放在 common 下共用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

__all__ = ["date_str", "bar_phase", "is_intraday", "closed_view", "last_date"]


def date_str(obj: Any) -> str:
    """把 日期对象 / Timestamp / 字符串 统一成 'YYYY-MM-DD'。

    yfinance 给的是带时区的 DatetimeIndex，兜底源是 `pd.to_datetime(...)` 的
    朴素索引，还有 `set_index("Date")` 的字符串索引 —— 三种都要能吃。
    """
    if obj is None:
        return ""
    try:
        return str(obj)[:10]
    except Exception:                                   # noqa: BLE001
        return ""


def bar_phase(last_bar: Any) -> str:
    """最后一根日线属于「盘中 / 今日收盘 / 收盘」，判不出来返回空串。

    用**美东时间**判定：`last_bar` 等于美东今天且未过 16:00 → 盘中。
    返回空串表示判不出来，调用方应保守处理（不剔除、不加标注），
    而不是猜一个 —— 猜错会让整块指标差一个交易日。
    """
    day = date_str(last_bar)
    if not day:
        return ""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:                                   # noqa: BLE001
        return ""
    if day != now_et.strftime("%Y-%m-%d"):
        return "收盘"
    return "盘中" if now_et.hour < 16 else "今日收盘"


def is_intraday(last_bar: Any) -> bool:
    """这根日线是不是「当天还没走完」的那根。"""
    return bar_phase(last_bar) == "盘中"


def last_date(obj: Any) -> str:
    """取序列/DataFrame 最后一行的日期（'YYYY-MM-DD'）。"""
    try:
        return date_str(obj.index[-1])
    except Exception:                                   # noqa: BLE001
        return ""


def closed_view(obj: Any, last_bar: Any = None):
    """返回「只含已收盘日线」的视图（Series / DataFrame 通用）。

    只在最后一根确实还没走完时才剔除；盘前（末行=前一交易日）与收盘后
    （末行=今天且已过 16:00）都原样返回。视图是切片，不改原对象。
    """
    day = date_str(last_bar) if last_bar is not None else last_date(obj)
    try:
        if is_intraday(day) and len(obj) > 1:
            return obj.iloc[:-1]
    except Exception:                                   # noqa: BLE001
        pass
    return obj
