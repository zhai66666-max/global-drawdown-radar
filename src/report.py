from __future__ import annotations
"""
Report Generator — Jinja2 context builder + HTML renderer.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.config import TEMPLATE_DIR, ETFS, THRESHOLDS

logger = logging.getLogger(__name__)

# ─── Jinja2 setup ──────────────────────────────────────────────────────────────

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=True,
)


def _fmt_pct(val: float | None, decimals: int = 1) -> str:
    """Format a fraction as percentage string. e.g., -0.152 -> '-15.2%'"""
    if val is None:
        return "N/A"
    sign = "+" if val > 0 else ""
    return f"{sign}{val * 100:.{decimals}f}%"


def _fmt_price(val: float | None) -> str:
    """Format price with appropriate precision."""
    if val is None:
        return "N/A"
    if val >= 100:
        return f"{val:,.1f}"
    return f"{val:,.2f}"


def _fmt_days(val: int | None) -> str:
    """Format days count."""
    if val is None:
        return "N/A"
    return f"{val}天"


def _fmt_pctile(val: float | None) -> str:
    """Format percentile."""
    if val is None:
        return "N/A"
    return f"{val:.0f}%"


def build_report_context(
    metrics: list[dict],
    alerts: list[dict],
    errors: dict[str, str],
    run_ts_beijing: str,
) -> dict:
    """
    Build Jinja2 template context from raw metrics and alerts.
    """
    # ── Dashboard summary ──────────────────────────────────────────────────
    deep_count = sum(1 for m in metrics if m.get("dd_historical") is not None and abs(m["dd_historical"]) >= 0.30)
    historic_count = sum(1 for m in metrics if m.get("dd_historical") is not None and abs(m["dd_historical"]) >= 0.40)
    normal_count = sum(1 for m in metrics if m.get("dd_historical") is not None and abs(m["dd_historical"]) < 0.20)

    worst_daily = sorted(
        [m for m in metrics if m.get("daily_change_pct") is not None],
        key=lambda m: m["daily_change_pct"],
    )[:3]

    worst_dd = sorted(
        [m for m in metrics if m.get("dd_historical") is not None],
        key=lambda m: m["dd_historical"],
    )[:3]

    worst_pctile = sorted(
        [m for m in metrics if m.get("dd_percentile") is not None],
        key=lambda m: m["dd_percentile"],
        reverse=True,
    )[:3]

    # ── Enrich rows with display values ────────────────────────────────────
    rows = []
    for m in metrics:
        rows.append({
            **m,
            "price_display": _fmt_price(m["current_price"]),
            "change_display": _fmt_pct(m["daily_change_pct"]),
            "dd_52w_display": _fmt_pct(m["dd_52w"]),
            "max_dd_52w_display": _fmt_pct(m["max_dd_52w"]),
            "dd_5y_display": _fmt_pct(m["dd_5y"]),
            "dd_historical_display": _fmt_pct(m["dd_historical"]),
            "max_dd_historical_display": _fmt_pct(m["max_dd_historical"]),
            "dd_percentile_display": _fmt_pctile(m["dd_percentile"]),
            "vol_20d_display": _fmt_pct(m["vol_20d"]) if m["vol_20d"] is not None else "N/A",
            "dist_from_ath_display": _fmt_pct(m["dist_from_ath"]),
            "days_display": _fmt_days(m["days_since_ath"]),
            # Color helpers for table
            "change_color": "#22c55e" if (m.get("daily_change_pct") or 0) >= 0 else "#ef4444",
        })

    # ── Has alerts? ────────────────────────────────────────────────────────
    has_alert = len(alerts) > 0
    has_historic = historic_count > 0

    # ── DeepSeek commentary (placeholder) ──────────────────────────────────
    market_commentary = None  # Will be filled if DeepSeek is available

    return {
        "run_ts": run_ts_beijing,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows": rows,
        "alerts": alerts,
        "has_alert": has_alert,
        "has_historic": has_historic,
        "errors": errors,
        "market_commentary": market_commentary,
        "summary": {
            "total": len(metrics),
            "deep_count": deep_count,
            "historic_count": historic_count,
            "normal_count": normal_count,
            "worst_daily": worst_daily,
            "worst_dd": worst_dd,
            "worst_pctile": worst_pctile,
        },
        "thresholds": [f"{int(t*100)}%" for t in THRESHOLDS],
    }


def render_html(context: dict) -> str:
    """Render the email HTML from template and context."""
    template = _env.get_template("report.html")
    return template.render(**context)
