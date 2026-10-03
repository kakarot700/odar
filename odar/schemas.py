"""ODAR typed schemas.

Strict, explicitly-typed data contracts shared across the engine:

* Task graph types (``TaskType``, ``TaskNode``, ``TaskStatus``).
* Anthropic-style tool-use wire types (``ToolSpec``, ``ModelEvent``).
* Retrieval / extraction / sandbox result envelopes.
* Audit verdicts and dialectical self-correction traces.
* A tiny JSON-schema subset validator used as a poka-yoke gate before any
  tool is executed (wrong shapes are rejected *before* they can fail deep
  inside a tool implementation).

Everything is plain-stdlib (dataclasses + enums) so the schemas import with
zero heavy dependencies.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


def new_id(prefix: str) -> str:
    """Generate a unique, human-sortable identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def utc_timestamp() -> float:
    return time.time()


# --------------------------------------------------------------------------- #
# Task graph types
# --------------------------------------------------------------------------- #
class TaskType(str, Enum):
    """Typed research-task categories used inside the DAG."""

    EXPLORATION = "EXPLORATION"
    DATA_EXTRACTION = "DATA_EXTRACTION"
    FALSIFICATION = "FALSIFICATION"
    SYNTHESIS = "SYNTHESIS"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class TaskNode:
    """A single typed node inside the research DAG."""

    task_id: str
    task_type: TaskType
    goal: str
    depends_on: List[str] = field(default_factory=list)
    payload: Dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "goal": self.goal,
            "depends_on": list(self.depends_on),
            "status": self.status.value,
            "error": self.error,
            "result": self.result
            if isinstance(self.result, (dict, list, str, int, float, bool, type(None)))
            else str(self.result),
        }


# --------------------------------------------------------------------------- #
# Anthropic-style tool-use wire types
# --------------------------------------------------------------------------- #
class StopReason(str, Enum):
    """Mirror of Anthropic ``stop_reason`` values plus ODAR circuit breakers."""

    TOOL_USE = "tool_use"
    END_TURN = "end_turn"
    MAX_ITERATIONS = "max_iterations"
    QUALITY_PLATEAU = "quality_plateau"
    ERROR = "error"


@dataclass
class ToolSpec:
    """A tool definition with an explicit JSON schema (strict types)."""

    name: str
    description: str
    input_schema: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class ModelEvent:
    """One assistant turn in the Anthropic message lifecycle.

    ``content`` holds wire-format blocks (``{"type": "text"|"tool_use", ...}``)
    and ``stop_reason`` is one of :class:`StopReason` (or the raw API string
    when bridged from the real Messages API).
    """

    role: str
    content: List[Dict[str, Any]]
    stop_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "content": self.content, "stop_reason": self.stop_reason}


_TYPE_MAP: Dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate_payload(schema: Dict[str, Any], payload: Any) -> List[str]:
    """Validate ``payload`` against a JSON-schema subset.

    Supports: ``type``, ``required``, ``properties``, ``additionalProperties``,
    ``items``, ``minLength``, ``minimum``, ``maximum``.  Returns a list of
    human-readable violation strings; an empty list means the payload is safe.
    This is the poka-yoke gate that prevents malformed tool inputs from ever
    reaching tool implementations.
    """

    errors: List[str] = []

    def check(sch: Dict[str, Any], value: Any, path: str) -> None:
        expected = sch.get("type")
        if expected:
            py_type = _TYPE_MAP.get(expected)
            if py_type is not None:
                is_bool = isinstance(value, bool)
                if expected in ("integer", "number"):
                    ok = isinstance(value, py_type) and not is_bool
                elif expected == "boolean":
                    ok = is_bool
                else:
                    ok = isinstance(value, py_type) and not (
                        expected != "boolean" and is_bool and expected == "boolean"
                    )
                if not ok:
                    errors.append(f"{path}: expected type '{expected}', got {type(value).__name__}")
                    return
        if isinstance(value, str):
            min_length = sch.get("minLength")
            if min_length is not None and len(value) < min_length:
                errors.append(f"{path}: string shorter than minLength={min_length}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = sch.get("minimum")
            if minimum is not None and value < minimum:
                errors.append(f"{path}: value below minimum={minimum}")
            maximum = sch.get("maximum")
            if maximum is not None and value > maximum:
                errors.append(f"{path}: value above maximum={maximum}")
        if isinstance(value, dict):
            for req in sch.get("required", []):
                if req not in value:
                    errors.append(f"{path}.{req}: missing required property")
            properties = sch.get("properties", {})
            if sch.get("additionalProperties") is False:
                for key in value:
                    if key not in properties:
                        errors.append(f"{path}.{key}: additional property not allowed")
            for key, sub_schema in properties.items():
                if key in value:
                    check(sub_schema, value[key], f"{path}.{key}")
        if isinstance(value, list):
            items_schema = sch.get("items")
            if items_schema:
                for i, item in enumerate(value):
                    check(items_schema, item, f"{path}[{i}]")

    check(schema, payload, "$")
    return errors


# --------------------------------------------------------------------------- #
# Retrieval / extraction envelopes
# --------------------------------------------------------------------------- #
@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str
    engine: str

    def to_dict(self) -> Dict[str, Any]:
        return {"url": self.url, "title": self.title, "snippet": self.snippet, "engine": self.engine}


@dataclass
class ExtractedPage:
    url: str
    ok: bool
    text: str = ""
    title: str = ""
    chars: int = 0
    engine: str = ""
    error: Optional[str] = None
    resolved_url: str = ""
    http_status: Optional[int] = None
    content_hash: str = ""
    content_type: str = ""
    quarantined: bool = False
    injection_findings: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "ok": self.ok,
            "title": self.title,
            "chars": self.chars,
            "engine": self.engine,
            "error": self.error,
            "resolved_url": self.resolved_url or self.url,
            "http_status": self.http_status,
            "content_hash": self.content_hash,
            "content_type": self.content_type,
            "quarantined": self.quarantined,
            "injection_findings": self.injection_findings,
            "text": self.text,
        }


# --------------------------------------------------------------------------- #
# Audit verdicts
# --------------------------------------------------------------------------- #
class Verdict(str, Enum):
    ENTAILMENT = "ENTAILMENT"
    CONTRADICTION = "CONTRADICTION"
    NEUTRAL = "NEUTRAL"


@dataclass
class ClaimAuditResult:
    claim: str
    verdict: Verdict
    entailment_probability: float
    contradiction_probability: float
    threshold: float
    spans_checked: int
    certified: bool
    scorer_backend: str
    provisional: bool = False
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    note: str = ""

    def __post_init__(self) -> None:
        # INVARIANT (defense layer 1): a provisional/heuristic audit can
        # NEVER be a certification, no matter what the caller computed.
        if self.provisional and self.certified:
            self.certified = False
            self.note = (self.note + " | " if self.note else "") + (
                "certification suppressed: provisional scorer cannot certify"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim": self.claim,
            "verdict": self.verdict.value,
            "entailment_probability": round(self.entailment_probability, 4),
            "contradiction_probability": round(self.contradiction_probability, 4),
            "threshold": self.threshold,
            "spans_checked": self.spans_checked,
            "certified": self.certified,
            "scorer_backend": self.scorer_backend,
            "provisional": self.provisional,
            "note": self.note,
            "evidence": self.evidence,
        }


@dataclass
class SelfCorrectionTrace:
    """Full dialectical self-correction trace.

    Follows the mandated chain:
    Failed Hypothesis -> Diagnostic Detection -> Corrective Action ->
    Verified Synthesis.
    """

    hypothesis: str
    failed_hypothesis: str
    diagnostic_detection: str
    corrective_action: str
    verified_synthesis: str
    timestamp: float = field(default_factory=utc_timestamp)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hypothesis": self.hypothesis,
            "failed_hypothesis": self.failed_hypothesis,
            "diagnostic_detection": self.diagnostic_detection,
            "corrective_action": self.corrective_action,
            "verified_synthesis": self.verified_synthesis,
            "timestamp": self.timestamp,
        }


# --------------------------------------------------------------------------- #
# Agent trace
# --------------------------------------------------------------------------- #
@dataclass
class AgentTrace:
    """Complete, inspectable record of one Anthropic-style tool loop run."""

    node_id: str
    goal: str
    iterations: int = 0
    stop_cause: Optional[str] = None
    quality_history: List[float] = field(default_factory=list)
    best_quality: float = float("-inf")
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    messages: List[Dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=utc_timestamp)
    duration_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "goal": self.goal,
            "iterations": self.iterations,
            "stop_cause": self.stop_cause,
            "quality_history": self.quality_history,
            "best_quality": self.best_quality if self.best_quality != float("-inf") else None,
            "tool_calls": self.tool_calls,
            "duration_s": round(self.duration_s, 3),
        }
