"""Failure-aware retry engine.

Retries are classified, never blanket:

========================  ====================================================
429 / rate-limited        exponential backoff + jitter, honours Retry-After
transient 5xx             bounded retry with backoff
timeout / connection reset bounded retry
schema / policy errors    **never** retried (deterministic failure)
injection / quarantine    **never** retried; source quarantined
unsafe URL (SSRF)         **never** retried
persistent failure        terminate cleanly with diagnostic state
========================  ====================================================
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional


class FailureClass(str, Enum):
    TRANSIENT = "transient"  # 5xx, connection reset, DNS blip
    RATE_LIMITED = "rate_limited"  # 429
    TIMEOUT = "timeout"
    PERMANENT = "permanent"  # 4xx (not 429), schema errors
    SECURITY = "security"  # injection, SSRF, quarantine
    UNKNOWN = "unknown"


@dataclass
class RetryDecision:
    should_retry: bool
    delay_s: float = 0.0
    reason: str = ""


def classify_status(status_code: int) -> FailureClass:
    if status_code == 429:
        return FailureClass.RATE_LIMITED
    if 500 <= status_code <= 599:
        return FailureClass.TRANSIENT
    if 400 <= status_code <= 499:
        return FailureClass.PERMANENT
    return FailureClass.UNKNOWN


def classify_exception(exc: BaseException) -> FailureClass:
    name = type(exc).__name__
    message = str(exc).lower()
    if "timeout" in name.lower() or "timeout" in message or "timed out" in message:
        return FailureClass.TIMEOUT
    if "ratelimit" in name.lower() or "429" in message:
        return FailureClass.RATE_LIMITED
    if any(token in name.lower() for token in ("connection", "reset", "dns", "resolve", "network")):
        return FailureClass.TRANSIENT
    from odar.url_safety import UnsafeURLError

    if isinstance(exc, UnsafeURLError):
        return FailureClass.SECURITY
    return FailureClass.UNKNOWN


class RetryPolicy:
    """Exponential backoff with jitter and failure-class awareness."""

    RETRYABLE = frozenset({FailureClass.TRANSIENT, FailureClass.RATE_LIMITED, FailureClass.TIMEOUT})

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        jitter: float = 0.3,
        governor: Optional[Any] = None,
    ) -> None:
        self.max_retries = int(max_retries)
        self.base_delay = float(base_delay)
        self.max_delay = float(max_delay)
        self.jitter = float(jitter)
        self.governor = governor

    # ------------------------------------------------------------------ #
    def decide(
        self, failure: FailureClass, attempt: int, retry_after: Optional[float] = None
    ) -> RetryDecision:
        if failure not in self.RETRYABLE:
            return RetryDecision(False, 0.0, f"non-retryable failure class: {failure.value}")
        if attempt >= self.max_retries:
            return RetryDecision(False, 0.0, f"retry budget exhausted ({self.max_retries})")
        delay = min(self.max_delay, self.base_delay * (2**attempt))
        if failure is FailureClass.RATE_LIMITED and retry_after is not None:
            delay = min(self.max_delay, max(delay, float(retry_after)))
        delay += random.uniform(0.0, self.jitter)
        return RetryDecision(True, delay, f"retrying {failure.value} (attempt {attempt + 1})")

    # ------------------------------------------------------------------ #
    def execute(
        self,
        operation: Callable[..., Any],
        *args: Any,
        status_of: Optional[Callable[[Any], Optional[int]]] = None,
        **kwargs: Any,
    ) -> Any:
        """Run ``operation`` with classified retries.

        ``status_of(result)`` may return an HTTP status for result-level
        classification; exceptions are classified by type/message.
        Non-retryable failures raise immediately (no silent retry loops).
        """
        attempt = 0
        while True:
            try:
                result = operation(*args, **kwargs)
            except Exception as exc:
                failure = classify_exception(exc)
                decision = self.decide(failure, attempt)
                if not decision.should_retry:
                    raise
                self._sleep_with_governor(decision.delay_s)
                attempt += 1
                continue
            status = status_of(result) if status_of else None
            if status is not None and status >= 400:
                failure = classify_status(status)
                decision = self.decide(failure, attempt)
                if not decision.should_retry:
                    return result  # permanent failure: surface to caller
                self._sleep_with_governor(decision.delay_s)
                attempt += 1
                continue
            return result

    def _sleep_with_governor(self, delay_s: float) -> None:
        if self.governor is not None:
            self.governor.approve_retry()
        time.sleep(delay_s)
