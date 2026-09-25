#!/usr/bin/env python3
"""口径自检：确认「派生指标只吃已收盘 bar」这一条真的被执行。

为什么必须单独有一个脚本
--------------------------------------------------------------------------
这条规则在**本机永远测不出违反**：
  · 本机 IP 会让 yfinance 限流 → 走官方兜底源
  · 而官方兜底源（NASDAQ historical）本来就要等收盘后才收录当天
  ⇒ 序列末行永远是「已收盘」那根，`intraday_dropped` 恒为 False，
    就算把 closed_view 整段删掉，本地渲染也全绿。

只有 GitHub Actions（美国出口、yfinance 正常）在美股盘中跑才会命中。
所以这里用**合成数据 + 打桩 `bar_basis.is_intraday`** 把盘中场景造出来。
2026-09-25 就是靠这条规则发现「页头 52 周分位用实时价、回撤用收盘价」
（96.3% ↔ 98.1% 乱跳），以及「10 区块各资产回撤随盘中跳动、02 区块却锁死」。

用法：PYTHONPATH="$PWD" python scripts/check_bar_basis.py
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from src.providers.common import bar_basis
from src.providers.drawdown_radar import drawdown as rd

FAILS: list[str] = []
TOTAL = 0


def check(name: str, got, want, tol: float | None = None) -> None:
    global TOTAL
    TOTAL += 1
    ok = (abs(got - want) <= tol) if (tol is not None and isinstance(got, (int, float))
                                      and isinstance(want, (int, float))) else (got == want)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(name)


def main() -> int:
    # ── A. bar_basis 纯函数
    print("── A. bar_basis ──")
    check("date_str(None)", bar_basis.date_str(None), "")
    check("date_str(带时区字符串)", bar_basis.date_str("2026-09-24 00:00:00+00:00"), "2026-09-24")
    check("bar_phase('')", bar_basis.bar_phase(""), "")
    check("bar_phase(None)", bar_basis.bar_phase(None), "")
    check("bar_phase(过去日期)", bar_basis.bar_phase("1999-01-01"), "收盘")
    check("is_intraday(过去日期)", bar_basis.is_intraday("1999-01-01"), False)
    check("closed_view(末行是过去) 不剔除",
          len(bar_basis.closed_view(pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-09-23", "2026-09-24"])))), 2)

    # ── B. 盘中 / 收盘两条路径
    # 合成 300 个交易日：先涨到 200，再跌到 122；末行(09-25)故意砸到 90（盘中未收盘）
    print("── B. compute_all_metrics 盘中 vs 收盘 ──")
    N = 300
    idx = pd.bdate_range(end="2026-09-25", periods=N)
    base = np.concatenate([np.linspace(100.0, 200.0, 260), np.linspace(200.0, 120.0, 41)[1:]])
    base[-1] = 90.0                                   # 盘中那根，未收盘
    v0923, v0924, v0925 = float(base[-3]), float(base[-2]), float(base[-1])
    adj = pd.DataFrame({"TESTX": base}, index=idx)
    latest = {"TESTX": v0925}

    dd_closed = v0924 / 200.0 - 1.0                   # -0.39（正确）
    dd_full = v0925 / 200.0 - 1.0                     # -0.55（错误）

    orig = bar_basis.is_intraday
    try:
        bar_basis.is_intraday = lambda last_bar: True   # 打桩成盘中
        m = rd.compute_all_metrics(adj, latest)[0]
    finally:
        bar_basis.is_intraday = orig

    check("盘中：intraday_dropped", m["intraday_dropped"], True)
    check("盘中：basis_date(回撤基准)", m["basis_date"], "2026-09-24")
    check("盘中：data_date(价格基准)", m["data_date"], "2026-09-25")
    check("盘中：dd_52w 用收盘口径", round(m["dd_52w"], 6), round(dd_closed, 6), tol=1e-6)
    check("盘中：dd_52w 未误用含盘中值", round(m["dd_52w"], 6) == round(dd_full, 6), False)
    check("盘中：dd_historical 用收盘口径", round(m["dd_historical"], 6), round(dd_closed, 6), tol=1e-6)
    check("盘中：max_dd_historical 未误用含盘中值",
          round(m["max_dd_historical"], 6) == round(dd_full, 6), False)
    # 涨跌幅的前收取自序列倒数第二行 → 必须传完整序列，截掉会让涨跌幅错一天
    check("盘中：daily_change_pct 锚 09-24", m["daily_change_pct"],
          round(v0925 / v0924 - 1.0, 4), tol=1e-9)
    check("盘中：daily_change_pct 未错成 09-23→09-24",
          m["daily_change_pct"] == round(v0924 / v0923 - 1.0, 4), False)

    try:
        bar_basis.is_intraday = lambda last_bar: False  # 打桩成已收盘
        m2 = rd.compute_all_metrics(adj, latest)[0]
    finally:
        bar_basis.is_intraday = orig

    check("收盘：intraday_dropped", m2["intraday_dropped"], False)
    check("收盘：basis_date", m2["basis_date"], "2026-09-25")
    check("收盘：dd_52w 含末行", round(m2["dd_52w"], 6), round(dd_full, 6), tol=1e-6)

    if FAILS:
        print(f"\n口径自检失败 {len(FAILS)}/{TOTAL} ❌ → {FAILS}")
        return 1
    print(f"\n口径自检 {TOTAL}/{TOTAL} 通过 ✅（含盘中分支打桩）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
