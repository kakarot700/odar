"""Role-based model routing with cross-model fallback (multi-agent mode).

Each agent role (planner, researcher-extraction, reflection, writer, judge)
gets an ordered list of models.  A call tries the role's first healthy
model; a transient failure (429 / 5xx / timeout / empty output / one 400)
moves on to the NEXT model immediately instead of sleeping on the same one.
A model that fails twice in a run is disabled for the rest of the run and
recorded, so a broken free route never loops.

Every attempt is governed: one ``approve_model_call`` per request and one
``approve_retry`` per fallback hop, so routing can never escape the budget.

Default routes target Token Harbor's ``:free`` catalogue (verified for tool
use and JSON on 2026-10-07).  ``deepseek-v4.1-flash:free`` is a reasoning
model that spends its whole token budget on hidden thinking and returns no
text at ordinary ``max_tokens``; it is excluded from defaults.  Override with
``ODAR_MODEL_ROUTES`` (JSON ``{"role": ["model", ...]}``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from odar.llm import (
    AuthBackendError,
    MalformedOutputError,
    ModelBackendError,
    classify_backend_error,
)

logger = logging.getLogger("odar.router")

ROLE_PLANNER = "planner"
ROLE_REFLECT = "reflect"
ROLE_WRITER = "writer"
ROLE_JUDGE = "judge"
ROLE_EXTRACT = "extract"
ROLES = (ROLE_PLANNER, ROLE_REFLECT, ROLE_WRITER, ROLE_JUDGE, ROLE_EXTRACT)

# Measured 2026-10-07 (bench/models.md): deepseek-v4-flash wrote a 600-word
# fully cited report in ~20 s; mimo-v2.5 ~40 s; mimo-v2.6-flash ~90 s.
DEFAULT_FREE_ROUTES: Dict[str, List[str]] = {
    ROLE_PLANNER: ["deepseek-v4-flash:free", "mimo-v2.5:free", "mimo-v2.6-flash:free"],
    ROLE_REFLECT: ["deepseek-v4-flash:free", "mimo-v2.5:free", "mimo-v2.6-flash:free"],
    ROLE_WRITER: ["deepseek-v4-flash:free", "mimo-v2.5:free", "mimo-v2.6-flash:free"],
    ROLE_JUDGE: ["mimo-v2.5:free", "deepseek-v4-flash:free"],
    ROLE_EXTRACT: ["mimo-v2.5:free", "deepseek-v4-flash:free"],
}
MAX_FAILURES_PER_MODEL = 2


class TextClient(Protocol):
    def complete(self, model: str, prompt: str, system: str, max_tokens: int) -> str: ...


class AnthropicMessagesClient:
    """Thread-safe synchronous Messages API client (official SDK).

    Works against Anthropic or any Anthropic-compatible gateway
    (``base_url``).  SDK retries are disabled: the router owns fallback.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        import anthropic

        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        kwargs: Dict[str, Any] = {"api_key": key, "max_retries": 0, "timeout": float(timeout)}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**kwargs)

    def complete(self, model: str, prompt: str, system: str, max_tokens: int) -> str:
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        parts: List[str] = []
        for block in response.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(parts).strip()


def load_routes(env: Optional[Dict[str, str]] = None) -> Dict[str, List[str]]:
    """Default free routes, optionally overridden per role by
    ``ODAR_MODEL_ROUTES`` JSON.  Unknown roles and empty lists are ignored."""
    routes = {role: list(models) for role, models in DEFAULT_FREE_ROUTES.items()}
    raw = (env if env is not None else os.environ).get("ODAR_MODEL_ROUTES", "").strip()
    if not raw:
        return routes
    try:
        override = json.loads(raw)
    except ValueError:
        logger.warning("ODAR_MODEL_ROUTES is not valid JSON; using defaults")
        return routes
    if isinstance(override, dict):
        for role, models in override.items():
            if role in routes and isinstance(models, list):
                cleaned = [str(m).strip() for m in models if str(m).strip()]
                if cleaned:
                    routes[role] = cleaned
    return routes


@dataclass
class RouteAttempt:
    role: str
    model: str
    ok: bool
    seconds: float
    error: str = ""


@dataclass
class ModelRouter:
    client: TextClient
    governor: Any
    routes: Dict[str, List[str]] = field(default_factory=load_routes)
    max_failures_per_model: int = MAX_FAILURES_PER_MODEL
    sleep: Callable[[float], None] = time.sleep
    passes: int = 2  # full sweeps over a role's models before giving up

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self.failures: Dict[str, int] = {}
        self.disabled: Dict[str, str] = {}
        self.attempts: List[RouteAttempt] = []
        self.calls_by_role: Dict[str, int] = {}

    # ------------------------------------------------------------------ #
    def healthy_models(self, role: str) -> List[str]:
        models = self.routes.get(role) or self.routes.get(ROLE_WRITER) or []
        with self._lock:
            return [m for m in models if m not in self.disabled]

    def _record(self, attempt: RouteAttempt) -> None:
        with self._lock:
            self.attempts.append(attempt)
            if attempt.ok:
                self.calls_by_role[attempt.role] = self.calls_by_role.get(attempt.role, 0) + 1
                return
            self.failures[attempt.model] = self.failures.get(attempt.model, 0) + 1
            if (
                self.failures[attempt.model] >= self.max_failures_per_model
                and attempt.model not in self.disabled
            ):
                self.disabled[attempt.model] = attempt.error[:160]
                logger.warning("model %s disabled for this run: %s", attempt.model, attempt.error[:160])

    def complete(
        self,
        role: str,
        prompt: str,
        system: str,
        max_tokens: int = 1024,
        validator: Optional[Callable[[str], bool]] = None,
    ) -> str:
        """Governed completion for ``role`` with cross-model fallback.

        Raises ``AuthBackendError`` at once (same key for every model) and
        ``ModelBackendError`` once every model of the role has failed.
        ``BudgetExceeded`` from the governor always propagates.
        """
        last_error: Optional[ModelBackendError] = None
        hops = 0
        for sweep in range(self.passes):
            models = self.healthy_models(role)
            if not models:
                break
            if sweep:
                self.sleep(2.0 * sweep)  # one short pause before the second sweep
            for model in models:
                if hops:
                    self.governor.approve_retry()
                self.governor.approve_model_call()
                hops += 1
                started = time.monotonic()
                try:
                    text = self.client.complete(model, prompt, system, max_tokens)
                    if not text.strip():
                        raise MalformedOutputError(f"{model} returned no text")
                    if validator is not None and not validator(text):
                        raise MalformedOutputError(f"{model} returned unusable output for {role}")
                except AuthBackendError:
                    raise
                except Exception as exc:  # noqa: BLE001 - classified below
                    from odar.budget import BudgetExceeded, CancelledError

                    if isinstance(exc, (BudgetExceeded, CancelledError)):
                        raise
                    error = exc if isinstance(exc, ModelBackendError) else classify_backend_error(exc)
                    if isinstance(error, AuthBackendError):
                        raise error from exc
                    self._record(RouteAttempt(role, model, False, time.monotonic() - started, str(error)))
                    last_error = error
                    continue
                self._record(RouteAttempt(role, model, True, time.monotonic() - started))
                return text
        raise last_error or ModelBackendError(f"no healthy model for role {role!r}")

    def report(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "routes": {r: list(m) for r, m in self.routes.items()},
                "calls_by_role": dict(self.calls_by_role),
                "failures": dict(self.failures),
                "disabled": dict(self.disabled),
                "attempts": [a.__dict__ for a in self.attempts[-40:]],
            }


class RouterController:
    """Adapter giving the router the ``complete_text`` interface used by
    ``LLMRefutationJudge`` and the single-pass synthesiser."""

    backend = "multi-agent-router"

    def __init__(self, router: ModelRouter, role: str = ROLE_JUDGE) -> None:
        self.router = router
        self.role = role

    def complete_text(self, prompt: str, system_prompt: str, governor: Any, max_tokens: int = 1024) -> str:
        return self.router.complete(self.role, prompt, system_prompt, max_tokens=max_tokens)
