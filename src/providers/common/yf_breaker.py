"""yfinance 熔断器（进程级）。

问题：Yahoo 一旦对本机/IP 限流（YFRateLimitError），
后续每一次 `yf.Ticker(x).history()` 都还会各自重试并等待，
40 只成分股 + 6 个宏观指标 + 1 个指数 会让一次运行白等好几分钟。

做法：连续失败若干次后直接把 yfinance 判为「本轮不可用」，
后续调用立即短路去兜底源。成功一次即复位。
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

THRESHOLD = 4                    # 连续失败多少次后熔断

_lock = threading.Lock()
_consecutive_failures = 0
_open = False
_announced = False


def is_open() -> bool:
    """True = 已熔断，不要再尝试 yfinance。"""
    with _lock:
        return _open


def record_success() -> None:
    global _consecutive_failures, _open, _announced
    with _lock:
        _consecutive_failures = 0
        if _open:
            logger.info("yfinance 恢复正常，熔断解除")
        _open = False
        _announced = False


def record_failure(reason: str = "") -> None:
    global _consecutive_failures, _open, _announced
    with _lock:
        _consecutive_failures += 1
        if _consecutive_failures >= THRESHOLD and not _open:
            _open = True
            if not _announced:
                logger.warning("yfinance 连续失败 %d 次，本轮熔断，后续直接走兜底源（%s）",
                               _consecutive_failures, reason[:120])
                _announced = True


def reset() -> None:
    """测试用。"""
    global _consecutive_failures, _open, _announced
    with _lock:
        _consecutive_failures = 0
        _open = False
        _announced = False


def stats() -> dict:
    with _lock:
        return {"fails": _consecutive_failures, "open": _open}
