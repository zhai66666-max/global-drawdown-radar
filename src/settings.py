"""统一配置解析。

三个旧项目各自用了不同的环境变量命名，这里全部兼容，不要求你重新配 Secrets：

  方案 A（nasdaq-etf-monitor）: EMAIL_HOST / EMAIL_PORT / EMAIL_USERNAME /
                               EMAIL_PASSWORD / EMAIL_TO
  方案 B（drawdown-radar & nasdaq100）: SMTP_SERVER / SMTP_PORT / SENDER_EMAIL /
                                       SENDER_PASSWORD / RECIPIENT_EMAIL / TEST_RECIPIENT

两个方案的变量只要存在任意一套，邮件就能发出去。同时存在时以方案 A 为准。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.paths import CONFIG_DIR

def _truthy(v) -> bool:
    """解析布尔值，兼容 '1/true/yes/on'。"""
    return (v or "").strip().lower() in {"1", "true", "yes", "on"}


def load_display() -> dict:
    """读取 config/display.yaml"""
    path = CONFIG_DIR / "display.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    recipients: list[str] = field(default_factory=list)
    test_recipient: str = ""
    scheme: str = "unknown"          # 'A' | 'B' | 'unknown'
    complete: bool = False
    missing: list[str] = field(default_factory=list)

    @property
    def use_ssl(self) -> bool:
        """465 用隐式 SSL，587/25 用 STARTTLS。"""
        return int(self.port) == 465

    def mask(self) -> str:
        return f"{self.host}:{self.port} [方案{self.scheme}] 发件人={_mask_email(self.username)} " \
               f"收件人={[_mask_email(r) for r in self.recipients]}"


def _mask_email(addr: str) -> str:
    if not addr or "@" not in addr:
        return addr or "(空)"
    name, _, domain = addr.partition("@")
    keep = name[:2]
    return f"{keep}{'*' * max(1, len(name) - 2)}@{domain}"


def _blocklist() -> frozenset[str]:
    """收件人黑名单（沿用 nasdaq-etf-monitor 的机制）。"""
    raw = os.environ.get("EMAIL_BLOCKLIST", "1741534484@qq.com")
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def load_smtp() -> SmtpConfig:
    """按优先级解析两套 Secrets 命名。"""
    # 方案 A
    a_any = any(os.environ.get(k) for k in
                ("EMAIL_HOST", "EMAIL_USERNAME", "EMAIL_PASSWORD", "EMAIL_TO"))
    if a_any:
        username = os.environ.get("EMAIL_USERNAME", "").strip()
        password = os.environ.get("EMAIL_PASSWORD", "").strip()
        host = os.environ.get("EMAIL_HOST", "smtp.qq.com").strip()
        port = int(os.environ.get("EMAIL_PORT", "465") or 465)
        raw_to = os.environ.get("EMAIL_TO", "")
        scheme = "A"
    else:
        username = os.environ.get("SENDER_EMAIL", "").strip()
        password = os.environ.get("SENDER_PASSWORD", "").strip()
        host = os.environ.get("SMTP_SERVER") or os.environ.get("SMTP_HOST") or "smtp.gmail.com"
        host = host.strip()
        port = int(os.environ.get("SMTP_PORT", "587") or 587)
        raw_to = os.environ.get("RECIPIENT_EMAIL", "")
        scheme = "B"

    configured = [e.strip() for e in raw_to.replace(";", ",").split(",") if e.strip()]
    blocked = _blocklist()
    recipients = [e for e in configured if e.lower() not in blocked]

    missing: list[str] = []
    if not host:
        missing.append("EMAIL_HOST / SMTP_SERVER")
    if not username:
        missing.append("EMAIL_USERNAME / SENDER_EMAIL")
    if not password:
        missing.append("EMAIL_PASSWORD / SENDER_PASSWORD")
    if not recipients:
        if configured:
            missing.append("收件人全部命中黑名单（EMAIL_BLOCKLIST）")
        else:
            missing.append("EMAIL_TO / RECIPIENT_EMAIL")

    return SmtpConfig(
        host=host,
        port=port,
        username=username,
        password=password,
        recipients=recipients,
        test_recipient=os.environ.get("TEST_RECIPIENT", "").strip(),
        scheme=scheme,
        complete=not missing,
        missing=missing,
    )


# ─── DeepSeek ────────────────────────────────────────────────────────────────
DEEPSEEK_API = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"   # deepseek-chat 已于 2026-07-24 废弃


def deepseek_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def github_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
