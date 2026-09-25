"""统一发信层。

三件旧事合并成一件：
  1. 一套 SMTP 连接逻辑（465 隐式 SSL / 587 STARTTLS 自动判别）
  2. 失败重试 + 指数退避（网络抖动不该让简报丢掉）
  3. 纯文本兜底正文（部分邮件客户端不渲染 HTML）

被合并进来的三个旧项目各自带了 send_email，这里统一收口；
正文由 templates/brief.html 渲染，不再各自拼 HTML。
"""
from __future__ import annotations

import logging
import smtplib
import time
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

from src.settings import SmtpConfig

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
MAX_ATTEMPTS = 3
BACKOFF_BASE = 5          # 秒：5 → 10 → 20


def build_message(cfg: SmtpConfig, subject: str, html: str, text: str,
                  recipients: list[str]) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header("纳斯达克100 每日简报", "utf-8")), cfg.username))
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)

    # 纯文本在前、HTML 在后 —— 客户端优先选后面的 HTML
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def html_to_text(html: str) -> str:
    """极简 HTML → 纯文本，仅供不支持 HTML 的客户端兜底。"""
    import html as _html
    import re

    s = re.sub(r"(?is)<(script|style|head).*?</\1>", "", html)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|tr|table|h[1-6])>", "\n", s)
    s = re.sub(r"(?i)</t[dh]>", "  ", s)
    s = re.sub(r"(?s)<[^>]+>", "", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


def send(cfg: SmtpConfig, subject: str, html: str,
         recipients: list[str] | None = None,
         plain_text: str | None = None,
         attachments: dict[str, bytes] | None = None) -> bool:
    """发信。返回 True 表示成功。

    attachments: {文件名: 内容}，例如把当日 HTML 归档一并带上。
                 不传则只有正文。
    """
    if not cfg.complete:
        raise RuntimeError("SMTP 配置不完整，缺失：" + "、".join(cfg.missing))
    if cfg.test_recipient:                     # 测试模式覆盖收件人
        recipients = [cfg.test_recipient]
        logger.info("TEST_RECIPIENT 已设置，实际只发给 %s", cfg.test_recipient)
    recipients = recipients or cfg.recipients

    msg = build_message(cfg, subject, html, plain_text or html_to_text(html), recipients)

    if attachments:
        from email.mime.application import MIMEApplication
        for name, blob in attachments.items():
            part = MIMEApplication(blob, _subtype="html")
            part.add_header("Content-Disposition", "attachment",
                            filename=("utf-8", "", name))
            msg.attach(part)

    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            t0 = time.time()
            if cfg.use_ssl:
                with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=DEFAULT_TIMEOUT) as s:
                    s.login(cfg.username, cfg.password)
                    s.sendmail(cfg.username, recipients, msg.as_string())
            else:
                with smtplib.SMTP(cfg.host, cfg.port, timeout=DEFAULT_TIMEOUT) as s:
                    s.ehlo()
                    try:
                        s.starttls()
                        s.ehlo()
                    except smtplib.SMTPException as exc:
                        # 有些自建服务器不支持 STARTTLS，退回明文继续试
                        logger.warning("STARTTLS 不可用（%s），按明文继续", exc)
                    s.login(cfg.username, cfg.password)
                    s.sendmail(cfg.username, recipients, msg.as_string())
            logger.info("邮件已发送 → %s（%.1fs，第 %d 次尝试）",
                        recipients, time.time() - t0, attempt)
            return True
        except Exception as exc:                # noqa: BLE001 —— 网络类异常都值得重试
            last_err = exc
            if attempt < MAX_ATTEMPTS:
                wait = BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning("发送失败（第 %d 次）：%s —— %ds 后重试",
                               attempt, exc, wait)
                time.sleep(wait)

    logger.error("邮件发送最终失败：%s", last_err)
    return False


def dry_run_check(cfg: SmtpConfig) -> bool:
    """只连不发，验证账号密码可用（--check-smtp 用）。"""
    try:
        if cfg.use_ssl:
            with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=DEFAULT_TIMEOUT) as s:
                s.login(cfg.username, cfg.password)
        else:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=DEFAULT_TIMEOUT) as s:
                s.ehlo()
                try:
                    s.starttls()
                    s.ehlo()
                except smtplib.SMTPException:
                    pass
                s.login(cfg.username, cfg.password)
        return True
    except Exception as exc:                    # noqa: BLE001
        logger.error("SMTP 校验失败：%s", exc)
        return False
