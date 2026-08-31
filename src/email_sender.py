from __future__ import annotations
"""
Email Sender — Gmail SMTP with STARTTLS.
"""

import logging
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from src.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD

logger = logging.getLogger(__name__)


def build_subject(has_historic: bool, has_alert: bool, date_str: str) -> str:
    """Dynamic subject line based on alert severity."""
    if has_historic:
        return f"🚨 全球市场回撤雷达｜{date_str}｜07:21"
    elif has_alert:
        return f"⚠️ 全球市场回撤雷达｜{date_str}｜07:21"
    else:
        return f"🌍 全球市场回撤雷达｜{date_str}｜07:21"


def send_email(
    html_body: str,
    subject: str,
    recipient: str,
    sender: str | None = None,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
) -> bool:
    """
    Send HTML email via Gmail SMTP.

    Args:
      html_body: Full HTML content
      subject: Email subject line
      recipient: To address
      sender: From address (defaults to SMTP_USER)
      smtp_user: SMTP auth user (defaults to SMTP_USER)
      smtp_password: SMTP auth password (defaults to SMTP_PASSWORD)

    Returns:
      True if sent successfully.
    """
    smtp_user = smtp_user or SMTP_USER
    smtp_password = smtp_password or SMTP_PASSWORD
    sender = sender or smtp_user

    if not smtp_user or not smtp_password:
        logger.error("SMTP credentials not configured. Set SMTP_USER and SMTP_PASSWORD.")
        return False

    if not recipient:
        logger.error("No recipient configured. Set EMAIL_TO.")
        return False

    # ── Build message ──────────────────────────────────────────────────────
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = f"Global Drawdown Radar <{sender}>"
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # ── Send ───────────────────────────────────────────────────────────────
    try:
        logger.info("Connecting to %s:%d ...", SMTP_HOST, SMTP_PORT)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        logger.info("Email sent successfully to %s", recipient)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP authentication failed. Check Gmail App Password.")
        return False
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        return False
