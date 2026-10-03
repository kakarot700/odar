"""Health, readiness and configuration validation.

* ``liveness``  - process is alive and able to serve (always OK if importable).
* ``readiness`` - dependencies required to serve a request are present
                  (SQLite writable, retrieval constructible, sandbox usable).
* ``config validation`` - environment/config checked at startup; a bad config
  fails fast with an actionable message instead of mid-run surprises.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class HealthReport:
    status: str = "OK"  # OK | DEGRADED | FAIL
    checks: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "checks": self.checks}


def liveness() -> HealthReport:
    report = HealthReport()
    report.checks["process"] = {"ok": True}
    return report


def readiness(jobstore_path: str = "odar_jobs.db") -> HealthReport:
    report = HealthReport()

    # SQLite writable at the jobstore location.
    try:
        directory = os.path.dirname(os.path.abspath(jobstore_path)) or "."
        probe = os.path.join(directory, ".odar_health_probe")
        with open(probe, "w") as handle:
            handle.write("ok")
        os.unlink(probe)
        report.checks["storage"] = {"ok": True, "jobstore": jobstore_path}
    except OSError as exc:
        report.checks["storage"] = {"ok": False, "error": str(exc)}
        report.status = "FAIL"

    # Retrieval stack constructible (offline check only).
    try:
        from odar.retrieval import PageExtractor, ZeroCostSearch

        ZeroCostSearch()
        PageExtractor()
        report.checks["retrieval"] = {"ok": True}
    except Exception as exc:  # pragma: no cover - defensive
        report.checks["retrieval"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        report.status = "DEGRADED" if report.status == "OK" else report.status

    # Sandbox executable.
    try:
        from odar.sandbox import ExecutionSandbox

        digest = ExecutionSandbox(timeout=10.0).run_python("print(1)")
        report.checks["sandbox"] = {"ok": bool(digest.get("ok")), "stdout": digest.get("stdout", "")[:40]}
        if not digest.get("ok"):
            report.status = "DEGRADED"
    except Exception as exc:  # pragma: no cover - defensive
        report.checks["sandbox"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        report.status = "DEGRADED"

    # Model backend availability (informational, never fatal).
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    report.checks["model_backend"] = {
        "ok": True,
        "backend": "anthropic-sdk" if has_key else "scripted-reactive (offline)",
    }
    return report


def validate_config(config: Dict[str, Any]) -> List[str]:
    """Return a list of configuration problems (empty list = valid)."""
    problems: List[str] = []
    budget = config.get("budget", {})
    for key in ("max_iterations", "max_search_calls", "max_fetches", "max_model_calls"):
        value = budget.get(key)
        if value is not None and (not isinstance(value, int) or value <= 0):
            problems.append(f"budget.{key} must be a positive integer, got {value!r}")
    wall = budget.get("max_wall_clock_s")
    if wall is not None and (not isinstance(wall, (int, float)) or wall <= 0):
        problems.append(f"budget.max_wall_clock_s must be positive, got {wall!r}")
    model = config.get("model", "scripted")
    if model not in ("scripted", "llm"):
        problems.append(f"model must be 'scripted' or 'llm', got {model!r}")
    if model == "llm" and not os.environ.get("ANTHROPIC_API_KEY"):
        problems.append("model='llm' requires ANTHROPIC_API_KEY to be set")
    output_dir = config.get("output_dir")
    if output_dir and os.path.exists(output_dir) and not os.path.isdir(output_dir):
        problems.append(f"output_dir exists and is not a directory: {output_dir}")
    return problems
