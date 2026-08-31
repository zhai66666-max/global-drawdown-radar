#!/usr/bin/env python3
"""
Global Market Drawdown Radar — Main Entry Point

Usage:
  python -m src.main                 # Full run: fetch + email
  python -m src.main --preview       # Save preview.html, no email
  python -m src.main --send-test     # Send to test recipient
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# Load .env before importing config
load_dotenv()

from src.config import (
    ETF_TICKERS,
    EMAIL_TO,
    TEST_RECIPIENT,
    STATE_FILE,
    DEEPSEEK_API_KEY,
    DEEPSEEK_API,
    DEEPSEEK_MODEL,
)
from src.data_fetcher import fetch_all_etfs, validate_data
from src.drawdown import compute_all_metrics
from src.signal import detect_breaches
from src.state import load_state, save_state, commit_and_push_state
from src.report import build_report_context, render_html
from src.email_sender import send_email, build_subject

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _beijing_now() -> str:
    """Return current Beijing time as readable string."""
    from datetime import timedelta
    bj = datetime.now(timezone.utc) + timedelta(hours=8)
    return bj.strftime("%Y-%m-%d %H:%M")


def _date_str() -> str:
    """Return current Beijing date string."""
    from datetime import timedelta
    bj = datetime.now(timezone.utc) + timedelta(hours=8)
    return bj.strftime("%Y-%m-%d")


def main():
    parser = argparse.ArgumentParser(description="Global Market Drawdown Radar")
    parser.add_argument(
        "--preview", action="store_true",
        help="Generate preview.html only, do not send email"
    )
    parser.add_argument(
        "--send-test", action="store_true",
        help="Send email to TEST_RECIPIENT instead of EMAIL_TO"
    )
    parser.add_argument(
        "--no-commit", action="store_true",
        help="Skip git commit of state.json (for local testing)"
    )
    args = parser.parse_args()

    run_ts = _beijing_now()
    date_str = _date_str()
    logger.info("=== 🌍 全球市场回撤雷达 === %s CST ===", run_ts)

    # ── 1. Fetch data ──────────────────────────────────────────────────────
    logger.info("[1/6] Fetching ETF data...")
    try:
        adj_close, errors, latest_close = fetch_all_etfs(ETF_TICKERS, period="max")
    except Exception as exc:
        logger.error("Fatal: data fetch failed: %s", exc)
        sys.exit(1)

    if adj_close.empty:
        logger.error("Fatal: no data fetched for any ETF")
        sys.exit(1)

    # Validate
    warnings = validate_data(adj_close)
    for w in warnings:
        logger.warning("  Data warning: %s", w)

    # ── 2. Compute metrics ─────────────────────────────────────────────────
    logger.info("[2/6] Computing drawdown metrics for %d ETFs...", len(adj_close.columns))
    metrics = compute_all_metrics(adj_close, latest_close)

    # ── 3. Detect signals ──────────────────────────────────────────────────
    logger.info("[3/6] Detecting threshold breaches...")
    state = load_state()
    alerts, updated_state = detect_breaches(metrics, state)

    # ── 4. Optional DeepSeek commentary ────────────────────────────────────
    market_commentary = None
    if DEEPSEEK_API_KEY and not args.preview:
        logger.info("[4/6] Generating DeepSeek market commentary...")
        try:
            import requests
            # Build summary for AI
            summary_lines = []
            for m in metrics:
                dd = m.get("dd_historical")
                change = m.get("daily_change_pct")
                if dd is not None:
                    summary_lines.append(
                        f"{m['ticker']}|{m['name_cn']}: "
                        f"历史回撤 {dd*100:.1f}%, 昨日 {change*100:.1f}%" if change is not None else f"{m['ticker']}|{m['name_cn']}: 历史回撤 {dd*100:.1f}%"
                    )
            data_summary = "\n".join(summary_lines)

            prompt = f"""你是全球宏观市场分析师。基于以下今日全球ETF回撤数据，用3-5句话做一个简洁的全球市场概览评论。

{data_summary}

要求：
1. 重点指出今日最值得关注的1-2个市场
2. 如果存在深度回撤（超过-30%）或历史级回撤（超过-40%）的市场，着重说明
3. 纯文本，不用markdown，中文输出，直接给出评论，不要多余的开场白"""

            resp = requests.post(
                DEEPSEEK_API,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": "你是全球宏观市场分析师，擅长用简洁专业的语言解读全球市场数据。"},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.4,
                    "max_tokens": 400,
                },
                timeout=60,
            )
            resp.raise_for_status()
            market_commentary = resp.json()["choices"][0]["message"]["content"]
            logger.info("DeepSeek commentary: %d chars", len(market_commentary))
        except Exception as exc:
            logger.warning("DeepSeek commentary failed (non-fatal): %s", exc)
    elif DEEPSEEK_API_KEY and args.preview:
        logger.info("[4/6] Skipping DeepSeek in preview mode")

    # ── 5. Generate report ─────────────────────────────────────────────────
    logger.info("[5/6] Generating HTML report...")
    context = build_report_context(metrics, alerts, errors, run_ts)
    if market_commentary:
        context["market_commentary"] = market_commentary
    html = render_html(context)
    logger.info("HTML: %.1f KB", len(html) / 1024)

    # ── 6. Preview or Send ─────────────────────────────────────────────────
    has_historic = context["has_historic"]
    has_alert = context["has_alert"]
    subject = build_subject(has_historic, has_alert, date_str)

    if args.preview:
        preview_path = Path("preview.html")
        preview_path.write_text(html, encoding="utf-8")
        logger.info("✅ Preview saved to %s (%d bytes)", preview_path, len(html))
        print(f"\n📊 Subject: {subject}")
        print(f"📄 Preview: {preview_path.absolute()}")
    else:
        recipient = TEST_RECIPIENT if args.send_test else EMAIL_TO
        if not recipient:
            logger.error("No recipient configured. Set EMAIL_TO or TEST_RECIPIENT.")
            sys.exit(1)

        logger.info("[6/6] Sending email to %s...", recipient)
        success = send_email(html, subject, recipient)
        if success:
            logger.info("✅ Email sent!")
        else:
            logger.error("❌ Email send failed")
            # Save HTML as artifact for debugging, then fail loudly
            preview_path = Path("preview.html")
            preview_path.write_text(html, encoding="utf-8")
            logger.info("Report saved to preview.html for debugging")
            sys.exit(2)

    # ── 7. Persist state ───────────────────────────────────────────────────
    save_state(updated_state)
    if not args.preview and not args.no_commit:
        commit_and_push_state()

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"🌍 全球市场回撤雷达 — {run_ts} CST")
    print(f"{'='*60}")
    print(f"ETF总数: {len(metrics)}  |  深度回撤: {context['summary']['deep_count']}  |  历史级: {context['summary']['historic_count']}")
    if alerts:
        print(f"\n🚨 新信号 ({len(alerts)}):")
        for a in alerts:
            print(f"  {a['ticker']}|{a['name_cn']}: 首次进入 -{int(a['threshold']*100)}% 区间 (当前 {a['current_dd']*100:.1f}%)")
    if errors:
        print(f"\n⚠️ 数据异常 ({len(errors)}):")
        for t, e in errors.items():
            print(f"  {t}: {e}")
    print(f"\nData: Yahoo Finance | 时间: {run_ts} CST")


if __name__ == "__main__":
    main()
