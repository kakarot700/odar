"""Resource budgets, governor gates and cancellation for bounded autonomy.

The ODAR Governor - not the model - decides whether an action is permitted.
Every research run executes under an explicit :class:`Budget`; every
side-effecting operation must pass :meth:`Governor.approve` first.  A
:class:`CancellationToken` propagates user cancellation through the loop,
network calls and sandbox processes.

Adaptive budgeting is implemented as *justified extensions*: the governor may
grant extra iterations only when the research state demonstrates a concrete
reason (unresolved contradiction, insufficient evidence, failed retrieval),
and every extension is capped and logged.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional


class BudgetExceeded(RuntimeError):
    """Raised when a governed action would exceed the run budget."""


class CancelledError(RuntimeError):
    """Raised when an operation is interrupted by cancellation."""


class CancellationRequested(Exception):
    """Checked variant used between loop turns (non-crashing signal)."""


class CancellationToken:
    """Thread-safe cancellation flag with deadline support."""

    def __init__(self, deadline: Optional[float] = None) -> None:
        self._event = threading.Event()
        self.deadline = deadline
        self.reason: Optional[str] = None

    def cancel(self, reason: str = "user_requested") -> None:
        self.reason = reason
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        if self._event.is_set():
            return True
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self.reason = "wall_clock_deadline"
            self._event.set()
            return True
        return False

    def check(self) -> None:
        """Raise :class:`CancelledError` if cancelled (call at safe points)."""
        if self.is_cancelled:
            raise CancelledError(self.reason or "cancelled")

    @property
    def remaining_seconds(self) -> Optional[float]:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())


@dataclass
class Budget:
    """Explicit, finite resource allowances for one research run."""

    max_iterations: int = 10
    max_tool_calls: int = 40
    max_search_calls: int = 12
    max_fetches: int = 8
    max_sandbox_executions: int = 8
    max_model_calls: int = 30
    max_wall_clock_s: float = 240.0
    max_response_chars: int = 18000
    max_retries_per_call: int = 3
    max_dependency_depth: int = 8
    max_concurrent_fetches: int = 2
    max_extensions: int = 2
    # NLI verifications (local cross-encoder, $0).  0 = legacy behaviour:
    # each verification consumes a model-call unit.  >0 = separate budget,
    # so model_calls counts real LLM requests only (multi-agent mode).
    max_verifications: int = 0

    def to_dict(self) -> Dict[str, float]:
        return {k: float(v) for k, v in self.__dict__.items()}


class Governor:
    """Validates and accounts every governed action.

    The agent proposes; the governor disposes.  No counter is incremented
    without passing approval, so a run can never exceed its budget silently.
    """

    def __init__(self, budget: Budget, token: Optional[CancellationToken] = None) -> None:
        self.budget = budget
        self.token = token or CancellationToken(deadline=time.monotonic() + budget.max_wall_clock_s)
        self._lock = threading.Lock()
        self.counters: Dict[str, int] = {
            "iterations": 0,
            "tool_calls": 0,
            "search_calls": 0,
            "fetches": 0,
            "sandbox_executions": 0,
            "model_calls": 0,
            "retries": 0,
            "verifications": 0,
        }
        self.denials: List[Dict[str, object]] = []
        self.extensions: List[Dict[str, object]] = []
        self._extensions_granted = 0

    # ------------------------------------------------------------------ #
    def _check_cancel(self) -> None:
        if self.token.is_cancelled:
            raise CancelledError(self.token.reason or "cancelled")

    def _deny(self, resource: str, detail: str) -> None:
        record = {"resource": resource, "detail": detail, "ts": time.time()}
        self.denials.append(record)
        raise BudgetExceeded(f"{resource} budget exhausted: {detail}")

    # ------------------------------------------------------------------ #
    def approve_iteration(self) -> int:
        self._check_cancel()
        with self._lock:
            if self.counters["iterations"] >= self.budget.max_iterations:
                self._deny("iterations", f"max {self.budget.max_iterations} reached")
            self.counters["iterations"] += 1
            return self.counters["iterations"]

    def approve_tool_call(self, tool_name: str) -> None:
        self._check_cancel()
        with self._lock:
            if self.counters["tool_calls"] >= self.budget.max_tool_calls:
                self._deny("tool_calls", f"max {self.budget.max_tool_calls} reached")
            self.counters["tool_calls"] += 1

    def approve_search(self) -> None:
        self._check_cancel()
        with self._lock:
            if self.counters["search_calls"] >= self.budget.max_search_calls:
                self._deny("search_calls", f"max {self.budget.max_search_calls} reached")
            self.counters["search_calls"] += 1

    def approve_fetch(self) -> None:
        self._check_cancel()
        with self._lock:
            if self.counters["fetches"] >= self.budget.max_fetches:
                self._deny("fetches", f"max {self.budget.max_fetches} reached")
            self.counters["fetches"] += 1

    def approve_sandbox(self) -> None:
        self._check_cancel()
        with self._lock:
            if self.counters["sandbox_executions"] >= self.budget.max_sandbox_executions:
                self._deny("sandbox_executions", f"max {self.budget.max_sandbox_executions} reached")
            self.counters["sandbox_executions"] += 1

    def approve_model_call(self) -> None:
        self._check_cancel()
        with self._lock:
            if self.counters["model_calls"] >= self.budget.max_model_calls:
                self._deny("model_calls", f"max {self.budget.max_model_calls} reached")
            self.counters["model_calls"] += 1

    def approve_verification(self) -> None:
        """One local NLI verification.  Uses its own budget when configured,
        otherwise falls back to the model-call budget (legacy accounting)."""
        if self.budget.max_verifications <= 0:
            self.approve_model_call()
            return
        self._check_cancel()
        with self._lock:
            if self.counters["verifications"] >= self.budget.max_verifications:
                self._deny("verifications", f"max {self.budget.max_verifications} reached")
            self.counters["verifications"] += 1

    def approve_retry(self) -> None:
        self._check_cancel()
        with self._lock:
            if self.counters["retries"] >= self.budget.max_retries_per_call * 4:
                self._deny("retries", "aggregate retry allowance exhausted")
            self.counters["retries"] += 1

    def check_depth(self, depth: int) -> None:
        if depth > self.budget.max_dependency_depth:
            self._deny("dependency_depth", f"depth {depth} > {self.budget.max_dependency_depth}")

    # ------------------------------------------------------------------ #
    def request_extension(self, justification: str, extra_iterations: int = 2) -> bool:
        """Adaptive budgeting: grant bounded extra iterations only with a
        machine-checkable justification, capped by ``max_extensions``."""
        allowed_reasons = (
            "unresolved_contradiction",
            "insufficient_evidence",
            "missing_primary_source",
            "inadequate_citation_coverage",
            "unsupported_critical_claim",
            "failed_source_retrieval",
        )
        with self._lock:
            if not any(justification.startswith(reason) for reason in allowed_reasons):
                self.denials.append(
                    {
                        "resource": "extension",
                        "detail": f"unrecognized justification: {justification}",
                        "ts": time.time(),
                    }
                )
                return False
            if self._extensions_granted >= self.budget.max_extensions:
                self.denials.append(
                    {"resource": "extension", "detail": "extension cap reached", "ts": time.time()}
                )
                return False
            self._extensions_granted += 1
            self.budget.max_iterations += max(1, min(extra_iterations, 3))
            self.extensions.append(
                {"justification": justification, "granted": extra_iterations, "ts": time.time()}
            )
            return True

    # ------------------------------------------------------------------ #
    def report(self) -> Dict[str, object]:
        return {
            "budget": self.budget.to_dict(),
            "counters": dict(self.counters),
            "denials": list(self.denials),
            "extensions": list(self.extensions),
            "cancelled": self.token.is_cancelled,
            "cancel_reason": self.token.reason,
        }
