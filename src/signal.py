from __future__ import annotations
"""
Signal Detection — threshold breach detection with cooldown.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config import THRESHOLDS, ALERT_COOLDOWN_DAYS
from src.state import load_state

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))


def _today_str() -> str:
    """统一使用北京时间（与报告/邮件一致）"""
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")


def detect_breaches(
    metrics: list[dict],
    state: dict[str, Any] | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """
    Compare current drawdowns against thresholds. Fire alerts on first breach
    or after cooldown. Update state.

    Args:
      metrics: list of ETF metric dicts from drawdown.compute_all_metrics
      state: current state dict (loaded from state.json)

    Returns:
      (new_alerts, updated_state)
    """
    if state is None:
        state = load_state()

    today = _today_str()
    alerts = state.setdefault("alerts", {})
    new_signals = []

    # 只跟踪这些指标；state.json 里残留的旧指标（如 dd_5y）自动清理
    TRACKED_METRICS = ("dd_historical",)

    # Also track "recovered" for signals that fell back below threshold
    recovered = []

    for m in metrics:
        ticker = m["ticker"]
        dd_historical = m.get("dd_historical")
        dd_5y = m.get("dd_5y")

        if ticker not in alerts:
            alerts[ticker] = {}

        # 清理不再跟踪的指标残留（死数据）
        stale_metrics = [k for k in alerts[ticker] if k not in TRACKED_METRICS]
        for sm in stale_metrics:
            del alerts[ticker][sm]

        for metric_name, dd_value in [("dd_historical", dd_historical)]:
            if dd_value is None:
                continue

            if metric_name not in alerts[ticker]:
                alerts[ticker][metric_name] = {}

            abs_dd = abs(dd_value)  # work with positive values

            for threshold in THRESHOLDS:
                t_key = str(threshold)
                if t_key not in alerts[ticker][metric_name]:
                    alerts[ticker][metric_name][t_key] = {
                        "last_alerted": None,
                        "last_value": None,
                        "breach_start": None,
                        "consecutive_days": 0,
                    }

                alert_state = alerts[ticker][metric_name][t_key]

                if abs_dd >= threshold:
                    # Currently in breach
                    alert_state["last_value"] = round(abs_dd, 4)
                    alert_state["consecutive_days"] = alert_state.get("consecutive_days", 0) + 1

                    if alert_state.get("breach_start") is None:
                        alert_state["breach_start"] = today

                    # Cooldown: only alert on first breach, or after ALERT_COOLDOWN_DAYS
                    last_alerted = alert_state.get("last_alerted")
                    should_alert = last_alerted is None
                    if not should_alert and last_alerted:
                        try:
                            days_since = (datetime.strptime(today, "%Y-%m-%d")
                                          - datetime.strptime(last_alerted, "%Y-%m-%d")).days
                        except ValueError:
                            days_since = ALERT_COOLDOWN_DAYS + 1
                        should_alert = days_since >= ALERT_COOLDOWN_DAYS

                    if should_alert:
                        alert_state["last_alerted"] = today
                        new_signals.append({
                            "ticker": ticker,
                            "name_cn": m["name_cn"],
                            "threshold": threshold,
                            "current_dd": round(abs_dd, 4),
                            "metric": metric_name,
                            "dd_percentile": m.get("dd_percentile"),
                            "breach_start": alert_state["breach_start"],
                        })
                        logger.info(
                            "🚨 %s %s breach: dd=%.1f%% >= %.0f%% threshold",
                            ticker, metric_name, abs_dd * 100, threshold * 100,
                        )
                    else:
                        logger.debug(
                            "⏳ %s %s still in breach (cooldown, %d days since last alert)",
                            ticker, metric_name, days_since,
                        )

                else:
                    # Not in breach — check if recovered
                    if alert_state.get("breach_start") is not None:
                        recovered.append({
                            "ticker": ticker,
                            "name_cn": m["name_cn"],
                            "threshold": threshold,
                            "metric": metric_name,
                            "breach_start": alert_state["breach_start"],
                            "recovered_date": today,
                        })
                        # Reset breach state
                        alert_state["last_alerted"] = None
                        alert_state["last_value"] = None
                        alert_state["breach_start"] = None
                        alert_state["consecutive_days"] = 0

    # Update last run timestamp (Beijing time)
    state["last_run_ts"] = datetime.now(BEIJING_TZ).isoformat()

    logger.info("Signals: %d new breaches, %d recoveries", len(new_signals), len(recovered))
    return new_signals, state
