"""Structured observability.

Every event carries correlation identifiers (request_id, run_id, node/tool
context) and is secret-redacted before recording.  Counters cover the
operational metrics the production checklist requires: call counts and
durations per tool, failure counts, retries, fallbacks, budget denials,
injection blocks, certification rate and unsupported-claim rate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from odar.schemas import new_id
from odar.trust import redact_secrets


@dataclass
class TelemetryEvent:
    name: str
    ts: float
    run_id: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "ts": self.ts, "run_id": self.run_id, "data": self.data}


class TelemetryRecorder:
    def __init__(self, request_id: Optional[str] = None) -> None:
        self.request_id = request_id or new_id("req")
        self.events: List[TelemetryEvent] = []
        self.counters: Dict[str, int] = {}
        self.latencies_ms: Dict[str, List[float]] = {}

    # ------------------------------------------------------------------ #
    def event(self, name: str, run_id: str = "", **data: Any) -> None:
        clean = {key: redact_secrets(str(value)) for key, value in data.items()}
        self.events.append(TelemetryEvent(name=name, ts=time.time(), run_id=run_id, data=clean))

    def count(self, metric: str, amount: int = 1) -> None:
        self.counters[metric] = self.counters.get(metric, 0) + amount

    def latency(self, tool: str, seconds: float) -> None:
        self.latencies_ms.setdefault(tool, []).append(seconds * 1000.0)

    # ------------------------------------------------------------------ #
    def summary(self) -> Dict[str, Any]:
        latency_summary: Dict[str, Dict[str, float]] = {}
        for tool, values in self.latencies_ms.items():
            ordered = sorted(values)
            latency_summary[tool] = {
                "count": len(values),
                "p50_ms": round(ordered[len(ordered) // 2], 2),
                "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
                "max_ms": round(ordered[-1], 2),
            }
        return {
            "request_id": self.request_id,
            "counters": dict(self.counters),
            "latencies": latency_summary,
            "event_count": len(self.events),
        }

    def dump_events(self, limit: int = 500) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self.events[-limit:]]
