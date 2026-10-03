"""Independent final audit.

The synthesis writer must not be the sole judge of its own correctness.
After any report is produced, :class:`FinalAuditor` - a component that never
sees the generation prompt and operates only on recorded state - checks the
report against the evidence trail.  Failures here block certification and
must be surfaced in the report itself (failure transparency).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from odar.evidence import Relation, Uncertainty
from odar.research_state import ResearchState
from odar.stats_extraction import extract_statistical_facts

_CAUSAL_RE = re.compile(
    r"\b(causes?|caused|proves?|proved|guarantees?|guaranteed|definitively|conclusively)\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")


@dataclass
class AuditReport:
    status: str = "PASS"  # PASS | PASS_WITH_FLAGS | FAIL
    checks: Dict[str, bool] = field(default_factory=dict)
    findings: List[str] = field(default_factory=list)
    unsupported_claims: List[str] = field(default_factory=list)
    hallucinated_citations: List[str] = field(default_factory=list)
    circular_citations: List[str] = field(default_factory=list)
    provisional_certifications: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "checks": dict(self.checks),
            "findings": self.findings,
            "unsupported_claims": self.unsupported_claims,
            "hallucinated_citations": self.hallucinated_citations,
            "circular_citations": self.circular_citations,
            "provisional_certifications": self.provisional_certifications,
        }


class FinalAuditor:
    """Post-synthesis verification against the recorded evidence trail."""

    def audit(self, state: ResearchState, synthesis_text: str) -> AuditReport:
        report = AuditReport()
        certified = state.supporting_claims()

        # 0. Provisional invariant: nothing verified only by a heuristic
        #    scorer may appear as certified - neither in state nor in text.
        for claim in state.claims.values():
            if claim.status == "CERTIFIED" and claim.provisional:
                report.provisional_certifications.append(claim.claim_id)
        for claim in state.claims.values():
            for e in state.evidence_for_claim(claim.claim_id):
                if claim.status == "CERTIFIED" and "PROVISIONAL" in (e.evaluated_by or ""):
                    if claim.claim_id not in report.provisional_certifications:
                        report.provisional_certifications.append(claim.claim_id)
        report.checks["no_provisional_certifications"] = not report.provisional_certifications

        # 1. Unsupported critical claims in the synthesis.  A non-certified
        #    claim may appear ONLY inside its explicitly labelled section
        #    (provisional / conflicts); anywhere else it is unsupported.
        def _section(header: str, stops: tuple) -> str:
            if header not in synthesis_text:
                return ""
            start = synthesis_text.index(header) + len(header)
            end = len(synthesis_text)
            for stop in stops:
                idx = synthesis_text.find(stop, start)
                if idx != -1:
                    end = min(end, idx)
            return synthesis_text[start:end]

        stops_for_provisional = (
            "## Contradicted",
            "## Contradictions Examined",
            "## Uncertainty and Limitations",
        )
        provisional_section = _section("## Provisional Findings", stops_for_provisional)
        conflicts_section = _section(
            "## Contradicted / Refuted / Conflicting",
            ("## Contradictions Examined", "## Uncertainty and Limitations"),
        )
        for claim in state.claims.values():
            if claim.status == "CERTIFIED":
                continue
            if claim.text and claim.text.lower()[:80] in synthesis_text.lower():
                disclosed = (claim.status == "PROVISIONAL" and claim.claim_id in provisional_section) or (
                    claim.status in ("CONFLICTING", "REFUTED") and claim.claim_id in conflicts_section
                )
                if not disclosed:
                    report.unsupported_claims.append(claim.claim_id)
        report.checks["no_unsupported_critical_claims"] = not report.unsupported_claims

        # 2. Hallucinated citations: every marker must resolve against the
        #    recorded research state (sources for src_*, claims for clm_*,
        #    evidence for ev_*).  No fabricated references.
        cited_sources = set(re.findall(r"\[(src_[a-z0-9]+)\]", synthesis_text))
        cited_claims = set(re.findall(r"\[(clm_[a-z0-9]+)\]", synthesis_text))
        cited_evidence = set(re.findall(r"\[(ev_[a-z0-9]+)\]", synthesis_text))
        unresolved = sorted(
            {c for c in cited_sources if c not in state.sources}
            | {c for c in cited_claims if c not in state.claims}
            | {c for c in cited_evidence if c not in state.evidence}
        )
        report.hallucinated_citations = unresolved
        report.checks["citations_resolve"] = not unresolved

        # 3. Circular citations: certified claims must not rely on
        #    CIRCULAR evidence (agent-origin self-support), and must have at
        #    least one genuinely supporting span.
        for claim in certified:
            items = state.evidence_for_claim(claim.claim_id)
            supporting = [e for e in items if e.relation is Relation.SUPPORTS]
            if any(e.relation is Relation.CIRCULAR for e in items) or not supporting:
                report.circular_citations.append(claim.claim_id)
        report.checks["no_circular_citations"] = not report.circular_citations

        # 4. Provenance integrity for certified claims.
        from odar.evidence import provenance_complete

        gaps: List[str] = []
        for claim in certified:
            for e in state.evidence_for_claim(claim.claim_id):
                source = state.sources.get(e.source_id)
                if source is None:
                    gaps.append(f"{claim.claim_id}: evidence {e.evidence_id} has no source record")
                else:
                    gaps.extend(provenance_complete(source, e))
        report.checks["provenance_intact"] = not gaps
        report.findings.extend(gaps[:12])

        # 5. Numeric consistency: numbers asserted in the synthesis must be
        #    grounded - either in the extracted statistical fact pool or
        #    verbatim in a source text.
        fact_numbers = set()
        source_text_blob = " ".join(s.extracted_text for s in state.sources.values())
        for source in state.sources.values():
            for fact in extract_statistical_facts(source.extracted_text):
                if fact.value is not None:
                    fact_numbers.add(fact.value)
        # Citation/evaluator metadata lines carry probabilities that are
        # already recorded in the evidence trail; they are exempt from the
        # factual-number grounding scan.
        factual_lines = [
            line for line in synthesis_text.splitlines() if not line.lstrip().startswith("- cited:")
        ]
        factual_text = "\n".join(factual_lines)
        stray: List[str] = []
        for token in set(_NUMBER_RE.findall(factual_text)):
            value = float(token)
            if value in fact_numbers:
                continue
            if any(abs(value - known) / max(known, 1e-9) < 0.02 for known in fact_numbers if known):
                continue
            if token in source_text_blob:
                continue  # verbatim grounding in a source
            if len(token) == 4 and 1800 <= value <= 2100:
                continue  # year reference
            if value <= 12 and value == int(value):
                continue  # small list/count ambiguity
            stray.append(token)
        report.checks["numeric_consistency"] = len(stray) <= 1
        if stray:
            report.findings.append(f"numbers in synthesis without grounding: {sorted(stray)[:8]}")

        # 6. Causal overstatement.
        causal_hits = sorted(set(m.group(0).lower() for m in _CAUSAL_RE.finditer(synthesis_text)))
        has_primary = any(s.publisher_class == "primary_research" for s in state.sources.values())
        report.checks["causal_language_appropriate"] = (not causal_hits) or has_primary
        if causal_hits and not has_primary:
            report.findings.append(f"causal language without primary-source backing: {causal_hits}")

        # 7. Contradictions examined.
        unexamined = [c for c in state.contradictions if not c.get("examined", False)]
        report.checks["contradictions_examined"] = not unexamined
        if unexamined:
            report.findings.append(f"unexamined contradictions: {len(unexamined)}")

        # 8. Uncertainty must be explicit (never silently confident).
        hedged = any(
            marker in synthesis_text.lower()
            for marker in ("uncertain", "limitation", "no evidence", "insufficient", "unknown")
        )
        abstaining = state.uncertainty in (
            Uncertainty.NO_EVIDENCE_FOUND,
            Uncertainty.INSUFFICIENT_EVIDENCE,
        )
        report.checks["uncertainty_explicit"] = hedged or abstaining or not state.claims

        failed = [name for name, ok in report.checks.items() if not ok]
        if any(
            name
            in (
                "citations_resolve",
                "no_circular_citations",
                "provenance_intact",
                "no_provisional_certifications",
            )
            for name in failed
        ):
            report.status = "FAIL"
        elif failed:
            report.status = "PASS_WITH_FLAGS"
        return report
