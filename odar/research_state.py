"""Stateful research state for the goal-driven research loop.

The agent loop is not a fixed pipeline: it adapts to what previous actions
returned.  :class:`ResearchState` is the single explicit record of the run -
objective, subquestions, hypotheses, claims, evidence, contradictions,
attempted approaches, budgets and termination status.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from odar.evidence import Claim, EvidenceItem, Relation, SourceRecord, Uncertainty
from odar.schemas import new_id


@dataclass
class ResearchState:
    objective: str
    run_id: str = field(default_factory=lambda: new_id("run"))
    subquestions: List[str] = field(default_factory=list)
    hypotheses: List[str] = field(default_factory=list)
    claims: Dict[str, Claim] = field(default_factory=dict)
    evidence: Dict[str, EvidenceItem] = field(default_factory=dict)
    sources: Dict[str, SourceRecord] = field(default_factory=dict)
    contradictions: List[Dict[str, Any]] = field(default_factory=list)
    unanswered: List[str] = field(default_factory=list)
    attempted_queries: List[str] = field(default_factory=list)
    attempted_actions: List[str] = field(default_factory=list)
    failed_approaches: List[str] = field(default_factory=list)
    fetched_urls: List[str] = field(default_factory=list)
    search_hit_urls: List[str] = field(default_factory=list)  # fetch allow-list (LLM path)
    mined_source_ids: List[str] = field(default_factory=list)  # sources already mined for claims
    quarantined_urls: List[str] = field(default_factory=list)
    injection_blocked: int = 0
    iteration: int = 0
    resume_probe: bool = False  # resumed run with no certified findings may probe again
    uncertainty: Uncertainty = Uncertainty.UNCERTAIN
    termination_status: str = (
        "RUNNING"  # RUNNING|COMPLETE|INCOMPLETE|BUDGET_EXHAUSTED|CANCELLED|FAILED|SECURITY_HALT
    )
    unmet_conditions: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------ #
    def record_query(self, query: str) -> None:
        if query and query not in self.attempted_queries:
            self.attempted_queries.append(query)

    def record_action(self, action: str) -> None:
        self.attempted_actions.append(f"iter{self.iteration}:{action}")

    def add_source(self, record: SourceRecord) -> None:
        self.sources[record.source_id] = record

    def add_claim(self, claim: Claim) -> None:
        self.claims[claim.claim_id] = claim

    def add_evidence(self, item: EvidenceItem) -> None:
        self.evidence[item.evidence_id] = item
        claim = self.claims.get(item.claim_id)
        if claim is not None and item.evidence_id not in claim.evidence_ids:
            claim.evidence_ids.append(item.evidence_id)

    def note(self, message: str) -> None:
        self.notes.append(f"iter{self.iteration}: {message}")

    # ------------------------------------------------------------------ #
    # Coverage metrics (termination-condition inputs)
    # ------------------------------------------------------------------ #
    def evidence_for_claim(self, claim_id: str) -> List[EvidenceItem]:
        return [e for e in self.evidence.values() if e.claim_id == claim_id]

    def supporting_claims(self) -> List[Claim]:
        """Claims with full (non-provisional) certification ONLY."""
        return [c for c in self.claims.values() if c.status == "CERTIFIED" and not c.provisional]

    def provisional_claims(self) -> List[Claim]:
        return [c for c in self.claims.values() if c.status == "PROVISIONAL" or c.provisional]

    def mark_new_source(self, source_id: str) -> None:
        """New evidence arrived: every claim whose last evaluation did not see
        this source becomes stale and MUST be re-evaluated (P1 invariant)."""
        for claim in self.claims.values():
            if source_id not in claim.evaluated_sources:
                claim.needs_evaluation = True

    def contradiction_pairs(self) -> List[Dict[str, Any]]:
        return list(self.contradictions)

    def relevant_evidence_count(self) -> int:
        return sum(
            1
            for e in self.evidence.values()
            if e.relation in (Relation.SUPPORTS, Relation.REFUTES, Relation.QUALIFIES)
        )

    def citation_coverage(self) -> Dict[str, bool]:
        """claim_id -> is every attached evidence item provenance-complete?"""
        coverage: Dict[str, bool] = {}
        for claim in self.claims.values():
            items = self.evidence_for_claim(claim.claim_id)
            coverage[claim.claim_id] = bool(items) and all(
                self.sources.get(e.source_id) is not None for e in items
            )
        return coverage

    def evidence_coverage(self) -> Dict[str, bool]:
        """claim_id -> has at least one semantically relevant evidence item."""
        coverage: Dict[str, bool] = {}
        for claim in self.claims.values():
            coverage[claim.claim_id] = any(
                e.relation in (Relation.SUPPORTS, Relation.REFUTES, Relation.QUALIFIES)
                for e in self.evidence_for_claim(claim.claim_id)
            )
        return coverage

    def independent_source_count(self) -> int:
        return sum(
            1
            for s in self.sources.values()
            if s.independent and s.duplicate_of is None and s.http_status is not None
        )

    # ------------------------------------------------------------------ #
    def digest(self, max_items: int = 8) -> Dict[str, Any]:
        """Compact machine-readable digest for model prompts and reports."""
        return {
            "objective": self.objective,
            "iteration": self.iteration,
            "termination_status": self.termination_status,
            "uncertainty": self.uncertainty.value,
            "subquestions": self.subquestions[:max_items],
            "hypotheses": self.hypotheses[:max_items],
            "claims": [
                {
                    "id": c.claim_id,
                    "text": c.text[:160],
                    "status": c.status,
                    "uncertainty": c.uncertainty.value,
                }
                for c in list(self.claims.values())[:max_items]
            ],
            "evidence_summary": {
                "total": len(self.evidence),
                "relevant": self.relevant_evidence_count(),
                "supports": sum(1 for e in self.evidence.values() if e.relation is Relation.SUPPORTS),
                "refutes": sum(1 for e in self.evidence.values() if e.relation is Relation.REFUTES),
                "qualifies": sum(1 for e in self.evidence.values() if e.relation is Relation.QUALIFIES),
                "circular_rejected": sum(
                    1 for e in self.evidence.values() if e.relation is Relation.CIRCULAR
                ),
            },
            "sources": {
                "total": len(self.sources),
                "independent": self.independent_source_count(),
                "classes": _count_classes(self.sources),
            },
            "contradictions": self.contradictions[:max_items],
            "unanswered": self.unanswered[:max_items],
            "attempted_queries": self.attempted_queries[-max_items:],
            "failed_approaches": self.failed_approaches[-max_items:],
            "evidence_coverage": self.evidence_coverage(),
            "citation_coverage": self.citation_coverage(),
            "quarantined_urls": len(self.quarantined_urls),
            "injection_blocked": self.injection_blocked,
        }

    # ------------------------------------------------------------------ #
    # Checkpointing (durable resume)
    # ------------------------------------------------------------------ #
    def to_checkpoint(self) -> Dict[str, Any]:
        return {
            "objective": self.objective,
            "run_id": self.run_id,
            "iteration": self.iteration,
            "resume_probe": self.resume_probe,
            "termination_status": self.termination_status,
            "uncertainty": self.uncertainty.value,
            "subquestions": self.subquestions,
            "hypotheses": self.hypotheses,
            "claims": [c.to_dict() for c in self.claims.values()],
            "evidence": [e.to_dict() for e in self.evidence.values()],
            "sources": [s.to_dict() for s in self.sources.values()],
            "contradictions": self.contradictions,
            "unanswered": self.unanswered,
            "attempted_queries": self.attempted_queries,
            "attempted_actions": self.attempted_actions,
            "failed_approaches": self.failed_approaches,
            "fetched_urls": self.fetched_urls,
            "search_hit_urls": self.search_hit_urls,
            "mined_source_ids": self.mined_source_ids,
            "quarantined_urls": self.quarantined_urls,
            "injection_blocked": self.injection_blocked,
            "unmet_conditions": self.unmet_conditions,
            "notes": self.notes[-50:],
        }

    @classmethod
    def from_checkpoint(cls, checkpoint: Dict[str, Any]) -> "ResearchState":
        from odar.evidence import Claim, EvidenceItem, Relation, SourceRecord, Uncertainty

        state = cls(objective=checkpoint.get("objective", ""), run_id=checkpoint.get("run_id", new_id("run")))
        state.iteration = int(checkpoint.get("iteration", 0))
        state.resume_probe = bool(checkpoint.get("resume_probe", False))
        state.termination_status = checkpoint.get("termination_status", "RUNNING")
        try:
            state.uncertainty = Uncertainty(checkpoint.get("uncertainty", "UNCERTAIN"))
        except ValueError:
            state.uncertainty = Uncertainty.UNCERTAIN
        state.subquestions = list(checkpoint.get("subquestions", []))
        state.hypotheses = list(checkpoint.get("hypotheses", []))
        state.contradictions = list(checkpoint.get("contradictions", []))
        state.unanswered = list(checkpoint.get("unanswered", []))
        state.attempted_queries = list(checkpoint.get("attempted_queries", []))
        state.attempted_actions = list(checkpoint.get("attempted_actions", []))
        state.failed_approaches = list(checkpoint.get("failed_approaches", []))
        state.fetched_urls = list(checkpoint.get("fetched_urls", []))
        state.search_hit_urls = list(checkpoint.get("search_hit_urls", []))
        state.mined_source_ids = list(checkpoint.get("mined_source_ids", []))
        state.quarantined_urls = list(checkpoint.get("quarantined_urls", []))
        state.injection_blocked = int(checkpoint.get("injection_blocked", 0))
        state.unmet_conditions = list(checkpoint.get("unmet_conditions", []))
        state.notes = list(checkpoint.get("notes", []))
        for source_dict in checkpoint.get("sources", []):
            try:
                record = SourceRecord(
                    source_id=source_dict["source_id"],
                    url=source_dict.get("url", ""),
                    resolved_url=source_dict.get("resolved_url", ""),
                    http_status=source_dict.get("http_status"),
                    retrieved_at=float(source_dict.get("retrieved_at", 0.0)),
                    title=source_dict.get("title", ""),
                    domain=source_dict.get("domain", ""),
                    publisher_class=source_dict.get("publisher_class", "unknown"),
                    publication_date=source_dict.get("publication_date"),
                    content_hash=source_dict.get("content_hash", ""),
                    content_chars=int(source_dict.get("content_chars", 0)),
                    content_type=source_dict.get("content_type", ""),
                    origin=source_dict.get("origin", "external"),
                    independent=bool(source_dict.get("independent", True)),
                    duplicate_of=source_dict.get("duplicate_of"),
                )
                state.sources[record.source_id] = record
            except (KeyError, TypeError, ValueError):
                continue
        for claim_dict in checkpoint.get("claims", []):
            try:
                state.claims[claim_dict["claim_id"]] = Claim(
                    claim_id=claim_dict["claim_id"],
                    text=claim_dict.get("text", ""),
                    origin=claim_dict.get("origin", "extraction"),
                    status=claim_dict.get("status", "PENDING"),
                    uncertainty=Uncertainty(claim_dict.get("uncertainty", "UNCERTAIN")),
                    confidence=float(claim_dict.get("confidence", 0.0)),
                    scope=claim_dict.get("scope", ""),
                    evidence_ids=list(claim_dict.get("evidence_ids", [])),
                    evaluated_sources=list(claim_dict.get("evaluated_sources", [])),
                    needs_evaluation=bool(claim_dict.get("needs_evaluation", True)),
                    provisional=bool(claim_dict.get("provisional", False)),
                )
            except (KeyError, ValueError):
                continue
        for evidence_dict in checkpoint.get("evidence", []):
            try:
                state.evidence[evidence_dict["evidence_id"]] = EvidenceItem(
                    evidence_id=evidence_dict["evidence_id"],
                    claim_id=evidence_dict["claim_id"],
                    source_id=evidence_dict["source_id"],
                    span=evidence_dict.get("span", ""),
                    relation=Relation(evidence_dict.get("relation", "IRRELEVANT")),
                    entailment_probability=float(evidence_dict.get("entailment_probability", 0.0)),
                    contradiction_probability=float(evidence_dict.get("contradiction_probability", 0.0)),
                    relevance_score=float(evidence_dict.get("relevance_score", 0.0)),
                    evaluated_by=evidence_dict.get("evaluated_by", ""),
                    circularity_score=float(evidence_dict.get("circularity_score", 0.0)),
                )
            except (KeyError, ValueError):
                continue
        return state


def _count_classes(sources: Dict[str, SourceRecord]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for source in sources.values():
        counts[source.publisher_class] = counts.get(source.publisher_class, 0) + 1
    return counts
