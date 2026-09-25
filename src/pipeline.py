"""统一编排层。

把原来三个独立项目各自的数据抓取 + 计算并行跑一遍，汇总成一个上下文。
三个来源互不阻塞：任何一个失败，其余两个照常产出，邮件照发，
失败的那个区块会显示明确的错误提示（而不是整封邮件挂掉）。

来源对应关系：
  etf_monitor     ← 原 nasdaq-etf-monitor      （纳指历史回撤 × 国内ETF溢价）
  drawdown_radar  ← 原 global-drawdown-radar   （全球 11 类资产回撤雷达）
  nasdaq100       ← 原 nasdaq100-daily-report  （纳指行情 / 宏观 / 技术面 / 成分股）
"""
from __future__ import annotations

import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.paths import DATA_DIR

logger = logging.getLogger(__name__)

BEIJING = timezone(timedelta(hours=8))


def beijing_now() -> datetime:
    return datetime.now(BEIJING)


def beijing_date_str() -> str:
    return beijing_now().strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SourceResult:
    key: str
    label: str
    ok: bool = False
    elapsed: float = 0.0
    error: str = ""
    data: dict = field(default_factory=dict)

    def summary(self) -> str:
        if self.ok:
            return f"{self.label}: OK ({self.elapsed:.1f}s)"
        return f"{self.label}: 失败 ({self.elapsed:.1f}s) — {self.error[:120]}"


# ── 来源 1：纳指历史回撤 × 国内 ETF 溢价 ─────────────────────────────────────

def collect_etf_monitor(run_date: str) -> dict:
    from src.providers.etf_monitor import (
        config_loader, data_fetcher, database, drawdown, etf, ranking,
    )

    # 1) 纳指100 完整历史（NASDAQ 官方 API → yfinance → 本地缓存，自动降级）
    ndx_df, ndx_source = data_fetcher.fetch_nasdaq_history()
    logger.info("  [etf_monitor] 纳指历史: %s, %d 条日线", ndx_source, len(ndx_df))

    # 2) 历史回撤
    strategy = config_loader.load_strategy()
    dd = drawdown.summary(
        ndx_df,
        levels=strategy["drawdown_levels"],
        thresholds=strategy["event_thresholds"],
    )
    logger.info("  [etf_monitor] 当前回撤 %.2f%% (分位 %.0f%%)",
                dd["current_drawdown"], dd["percentile"])

    # 3) 国内纳指 ETF 行情与溢价
    etf_cfg = config_loader.load_etfs()
    etf_configs = etf_cfg["etfs"]
    etf_results, ok_etfs, failed_etfs = etf.get_all_etfs(etf_configs)
    logger.info("  [etf_monitor] ETF: 成功 %d / 失败 %d", len(ok_etfs), len(failed_etfs))

    # 4) 排序与综合信号
    ranked = etf.rank_etfs(ok_etfs, strategy["premium_levels"], etf_cfg["liquidity"])
    signal = ranking.build_signal(dd, ranked, strategy["premium_accept_max"])
    recs = ranking.build_recommendations(ranked, etf_cfg["liquidity"])

    # 5) 落库（DuckDB 长期积累）
    try:
        conn = database.get_connection()
        database.init_db(conn)
        database.save_nasdaq(conn, dd["series"])
        database.save_etfs(conn, etf_results, run_date)
        database.save_drawdown_events(conn, dd["events"], run_date)
        conn.close()
    except Exception as exc:                       # 落库失败不影响发信
        logger.warning("  [etf_monitor] DuckDB 写入失败（不影响邮件）: %s", exc)

    basis_set = sorted({e["premium_basis"] for e in ok_etfs if e.get("premium_basis")})
    data_status = f"{len(ok_etfs)}/{len(etf_configs)} 只 ETF 成功"
    if failed_etfs:
        data_status += f"，{len(failed_etfs)} 只异常"

    # 精简 dd（扔掉 series / events 这类大对象，模板不需要）
    dd_slim = {
        "current_drawdown": dd["current_drawdown"],
        "current_peak": dd["current_peak"],
        "percentile": dd["percentile"],
        "level": dd["level"],
        "max_dd": dd["max_dd"],
        "recent_thresholds": dd["recent_thresholds"],
    }

    return {
        "dd": dd_slim,
        "ranked": ranked,
        "recommendations": recs,
        "signal": signal,
        "ndx_source": ndx_source,
        "ndx_count": len(ndx_df),
        "data_status": data_status,
        "basis_label": "、".join(basis_set) if basis_set else "未知",
        "failed_etfs": [
            {"code": f["code"], "name": f["name"], "error": f.get("error") or "无数据"}
            for f in failed_etfs
        ],
        "level_list": strategy["drawdown_levels"],
    }


# ── 来源 2：全球市场回撤雷达 ─────────────────────────────────────────────────

def collect_drawdown_radar() -> dict:
    from src.providers.drawdown_radar import data_fetcher as radar_fetch
    from src.providers.drawdown_radar.config import ETF_TICKERS
    from src.providers.drawdown_radar.data_fetcher import fetch_all_etfs, validate_data
    from src.providers.drawdown_radar.drawdown import compute_all_metrics
    from src.providers.drawdown_radar.signal import detect_breaches
    from src.providers.drawdown_radar.state import load_state, save_state

    adj_close, errors, latest_close = fetch_all_etfs(ETF_TICKERS, period="max")
    if adj_close is None or adj_close.empty:
        raise RuntimeError("全部 ETF 数据获取失败")

    for w in validate_data(adj_close):
        logger.warning("  [radar] 数据警告: %s", w)

    metrics = compute_all_metrics(adj_close, latest_close)
    logger.info("  [radar] 计算完成 %d 个资产", len(metrics))

    state = load_state()
    alerts, updated_state = detect_breaches(metrics, state)
    try:
        save_state(updated_state)
    except Exception as exc:
        logger.warning("  [radar] 状态保存失败（不影响邮件）: %s", exc)

    return {"metrics": metrics, "alerts": alerts, "errors": errors,
            "data_source": radar_fetch.LAST_SOURCE}


# ── 来源 3：纳斯达克100 深度数据 ─────────────────────────────────────────────

def collect_nasdaq100() -> dict:
    from src.providers.nasdaq100 import core

    ix = core.fetch_nasdaq100_data()
    logger.info("  [nasdaq100] 指数 %s (%+.2f%%)",
                f"{ix['current_price']:,.2f}", ix["change_pct"])

    macro_raw = core.fetch_all_macro_indicators()
    macro = [core.score_macro_indicator(v) for v in macro_raw.values() if v]
    logger.info("  [nasdaq100] 宏观指标 %d 项", len(macro))

    comps = core.fetch_top_components()
    logger.info("  [nasdaq100] 成分股 %d 只", len(comps))

    return {"ix": ix, "macro": macro, "components": comps,
            "components_source": core.LAST_COMPONENTS_SOURCE}


# ─────────────────────────────────────────────────────────────────────────────

SOURCES = [
    ("etf_monitor",    "纳指回撤 × ETF溢价", collect_etf_monitor),
    ("drawdown_radar", "全球回撤雷达",       collect_drawdown_radar),
    ("nasdaq100",      "纳指深度数据",       collect_nasdaq100),
]


def collect_all(run_date: str, max_workers: int = 3) -> dict[str, SourceResult]:
    """并发跑三个来源，单源失败被隔离。"""
    results: dict[str, SourceResult] = {}
    t_all = time.time()

    def _timed(fn, *args):
        """量每个来源自己的耗时。

        注意：不能用「as_completed 回调时刻 - 提交时刻」来算，
        那样算出来的是「等待其他来源的时间」，三个来源会得到近乎相同的假数值。
        必须在这里包一层，量函数自身的执行时间。
        """
        t0 = time.time()
        try:
            return fn(*args), None, time.time() - t0
        except Exception as exc:                # noqa: BLE001
            return None, exc, time.time() - t0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {}
        for key, label, fn in SOURCES:
            # etf_monitor 需要 run_date 参数
            args = (run_date,) if fn is collect_etf_monitor else ()
            futures[ex.submit(_timed, fn, *args)] = (key, label)

        for fut in as_completed(futures):
            key, label = futures[fut]
            data, exc, elapsed = fut.result()
            if exc is None:
                results[key] = SourceResult(key, label, ok=True,
                                            elapsed=elapsed, data=data)
            else:
                tb = traceback.format_exc(limit=3)
                logger.error("  [%s] 失败: %s", key, exc)
                logger.debug(tb)
                results[key] = SourceResult(key, label, ok=False, elapsed=elapsed,
                                            error=f"{type(exc).__name__}: {exc}")

    logger.info("全部来源耗时 %.1fs", time.time() - t_all)
    for r in results.values():
        logger.info("  %s", r.summary())
    return results
