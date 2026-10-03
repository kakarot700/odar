"""Structured evidence model, provenance chains and uncertainty taxonomy.

Every evidence item carries:

* a typed semantic relation to a claim (never bare keyword overlap),
* a full provenance chain (source URL -> resolved URL -> retrieval metadata
  -> exact span -> content hash -> evaluation -> certification),
* source-quality context,
* an explicit uncertainty classification.

Anti-circularity: an evidence span that is a near-duplicate of the claim it
is supposed to support is flagged ``CIRCULAR`` and can never certify that
claim.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from odar.loop_breaker import STOPWORDS, tokenize


class Relation(str, Enum):
    """Semantic relationship between an evidence span and a claim."""

    SUPPORTS = "SUPPORTS"
    REFUTES = "REFUTES"
    QUALIFIES = "QUALIFIES"
    IRRELEVANT = "IRRELEVANT"
    INSUFFICIENT = "INSUFFICIENT"
    CIRCULAR = "CIRCULAR"  # evidence duplicates the claim text itself


class Uncertainty(str, Enum):
    """Run/claim-level epistemic state.  Absence of evidence is NEVER the
    same as evidence of absence - the taxonomy keeps them distinct."""

    EVIDENCE_FOR = "EVIDENCE_FOR"
    EVIDENCE_AGAINST = "EVIDENCE_AGAINST"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_EVIDENCE_FOUND = "NO_EVIDENCE_FOUND"
    LOW_QUALITY_EVIDENCE = "LOW_QUALITY_EVIDENCE"
    OUTDATED_EVIDENCE = "OUTDATED_EVIDENCE"
    UNCERTAIN = "UNCERTAIN"


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()


def span_similarity(a: str, b: str) -> float:
    """Token-set similarity used for circularity checks (order-insensitive)."""
    tokens_a = {t for t in tokenize(a) if t not in STOPWORDS}
    tokens_b = {t for t in tokenize(b) if t not in STOPWORDS}
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


@dataclass
class SourceRecord:
    """Retrieval provenance for one fetched source."""

    source_id: str
    url: str
    resolved_url: str = ""
    http_status: Optional[int] = None
    retrieved_at: float = field(default_factory=time.time)
    title: str = ""
    domain: str = ""
    publisher_class: str = "unknown"  # see odar.source_quality.SourceClass
    publication_date: Optional[str] = None
    content_hash: str = ""
    content_chars: int = 0
    content_type: str = ""
    extracted_text: str = ""
    origin: str = "external"  # "external" | "agent" (agent = produced by ODAR itself)
    independent: bool = True
    duplicate_of: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.domain and self.url:
            self.domain = urlparse(self.url).netloc.lower()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "resolved_url": self.resolved_url or self.url,
            "http_status": self.http_status,
            "retrieved_at": self.retrieved_at,
            "title": self.title,
            "domain": self.domain,
            "publisher_class": self.publisher_class,
            "publication_date": self.publication_date,
            "content_hash": self.content_hash,
            "content_chars": self.content_chars,
            "content_type": self.content_type,
            "origin": self.origin,
            "independent": self.independent,
            "duplicate_of": self.duplicate_of,
        }


@dataclass
class EvidenceItem:
    """One evidence span bound to a claim with semantic evaluation."""

    evidence_id: str
    claim_id: str
    source_id: str
    span: str
    relation: Relation
    entailment_probability: float
    contradiction_probability: float
    relevance_score: float
    directionality: str = "unspecified"  # e.g. "improves", "worsens", "no_difference"
    uncertainty_note: str = ""
    span_hash: str = ""
    evaluated_by: str = ""  # scorer model/version
    evaluated_at: float = field(default_factory=time.time)
    circularity_score: float = 0.0

    def __post_init__(self) -> None:
        if not self.span_hash:
            self.span_hash = content_hash(self.span)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "claim_id": self.claim_id,
            "source_id": self.source_id,
            "span": self.span[:400],
            "span_hash": self.span_hash[:16],
            "relation": self.relation.value,
            "entailment_probability": round(self.entailment_probability, 4),
            "contradiction_probability": round(self.contradiction_probability, 4),
            "relevance_score": round(self.relevance_score, 4),
            "directionality": self.directionality,
            "uncertainty_note": self.uncertainty_note,
            "evaluated_by": self.evaluated_by,
            "evaluated_at": self.evaluated_at,
            "circularity_score": round(self.circularity_score, 4),
        }


@dataclass
class Claim:
    """A research claim with completeness bookkeeping."""

    claim_id: str
    text: str
    origin: str = "extraction"  # extraction | synthesis | model
    status: str = "PENDING"  # PENDING | CERTIFIED | PROVISIONAL | SUPPORTED | REFUTED |
    # CONFLICTING | CONTRADICTED | INSUFFICIENT | ABSTAINED
    uncertainty: Uncertainty = Uncertainty.UNCERTAIN
    confidence: float = 0.0
    scope: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    evaluated_sources: List[str] = field(default_factory=list)  # source_ids seen at last evaluation
    needs_evaluation: bool = True
    provisional: bool = False  # True when only heuristic verification was available

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "origin": self.origin,
            "status": self.status,
            "uncertainty": self.uncertainty.value,
            "confidence": round(self.confidence, 4),
            "scope": self.scope,
            "evidence_ids": list(self.evidence_ids),
            "evaluated_sources": list(self.evaluated_sources),
            "needs_evaluation": self.needs_evaluation,
            "provisional": self.provisional,
        }


CIRCULARITY_THRESHOLD = 0.9


def detect_circularity(claim_text: str, span_text: str) -> float:
    """Similarity between a claim and its purported evidence span.

    >= CIRCULARITY_THRESHOLD means the evidence *is* the claim (or a trivial
    paraphrase): self-support, rejected for certification.
    """
    return span_similarity(claim_text, span_text)


def provenance_complete(source: Optional[SourceRecord], item: EvidenceItem) -> List[str]:
    """Validate a provenance chain; returns the list of integrity gaps."""
    gaps: List[str] = []
    if source is None:
        gaps.append("source record missing")
    else:
        if not source.url:
            gaps.append("source url missing")
        if source.http_status is None:
            gaps.append("source http_status not recorded")
        if not source.content_hash:
            gaps.append("source content_hash not recorded")
    if not item.span.strip():
        gaps.append("evidence span empty")
    if not item.span_hash:
        gaps.append("span hash missing")
    if not item.evaluated_by:
        gaps.append("evaluator identity missing")
    if item.relation is Relation.CIRCULAR:
        gaps.append("circular evidence (claim duplicates span)")
    return gaps
