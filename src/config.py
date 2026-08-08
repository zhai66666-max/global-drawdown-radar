from __future__ import annotations
"""
Global Market Drawdown Radar — Configuration
All constants, ETF definitions, thresholds, and paths in one place.
"""

import os
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).parent.parent
STATE_FILE = ROOT_DIR / "data" / "state.json"
TEMPLATE_DIR = ROOT_DIR / "templates"

# ─── ETFs: ticker, display name, market, chinese name ──────────────────────────
# Fixed display order as specified
ETFS = [
    {"ticker": "QQQM", "name_cn": "纳斯达克100", "market": "美国科技股"},
    {"ticker": "SPY",  "name_cn": "标普500",     "market": "美国大盘"},
    {"ticker": "EWJ",  "name_cn": "日本",         "market": "日本"},
    {"ticker": "EWY",  "name_cn": "韩国",         "market": "韩国"},
    {"ticker": "INDA", "name_cn": "印度",         "market": "印度"},
    {"ticker": "EWT",  "name_cn": "台湾",         "market": "台湾"},
    {"ticker": "EWC",  "name_cn": "加拿大",       "market": "加拿大"},
    {"ticker": "EWW",  "name_cn": "墨西哥",       "market": "墨西哥"},
    {"ticker": "EWA",  "name_cn": "澳大利亚",     "market": "澳大利亚"},
    {"ticker": "EWZ",  "name_cn": "巴西",         "market": "巴西"},
    {"ticker": "GLD",  "name_cn": "黄金",         "market": "黄金"},
]

ETF_TICKERS = [e["ticker"] for e in ETFS]
ETF_LOOKUP = {e["ticker"]: e for e in ETFS}

# ─── Data windows ─────────────────────────────────────────────────────────────
TRADING_DAYS_52W = 252         # ~1 calendar year of trading days
TRADING_DAYS_5Y  = 252 * 5    # ~5 years of trading days (~1260)
VOLATILITY_WINDOW = 20          # 20 trading days for volatility

# ─── Drawdown thresholds (as positive fractions) ──────────────────────────────
# 0.20 = -20% drawdown, 0.30 = -30%, 0.40 = -40%
THRESHOLDS = [0.20, 0.30, 0.40]
ALERT_COOLDOWN_DAYS = 7  # Don't re-alert for same ETF+threshold within N days

# ─── Drawdown status labels ───────────────────────────────────────────────────
def get_drawdown_status(historical_dd: float | None) -> tuple[str, str, str]:
    """
    Returns (label, color, emoji) based on historical drawdown.
    historical_dd is a negative fraction, e.g. -0.25 = -25%.
    """
    if historical_dd is None:
        return ("N/A", "#9ca3af", "⚪")
    dd = abs(historical_dd)  # work with positive values
    if dd >= 0.40:
        return ("历史级回撤", "#ef4444", "🔴")
    elif dd >= 0.30:
        return ("深度回撤", "#f97316", "🟠")
    elif dd >= 0.20:
        return ("观察", "#f59e0b", "🟡")
    else:
        return ("正常", "#22c55e", "🟢")

# ─── Email ────────────────────────────────────────────────────────────────────
SMTP_HOST = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SENDER_EMAIL", "")
SMTP_PASSWORD = os.environ.get("SENDER_PASSWORD", "")
EMAIL_TO = os.environ.get("RECIPIENT_EMAIL", "")
TEST_RECIPIENT = os.environ.get("TEST_RECIPIENT", "")

# ─── DeepSeek (optional, for AI market commentary) ────────────────────────────
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"
