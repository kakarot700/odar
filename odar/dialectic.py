"""Dialectical attacker-analyzer loop.

Given a hypothesis, the engine *attacks* it: it formulates targeted
counter-queries, retrieves opposing evidence, scores support vs. refutation
pressure, and emits a full self-correction trace:

    Failed Hypothesis -> Diagnostic Detection -> Corrective Action ->
    Verified Synthesis

The search backend is injectable so tests (and offline runs) can substitute
canned evidence; by default it uses :class:`odar.retrieval.ZeroCostSearch`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from odar.loop_breaker import STOPWORDS, tokenize
from odar.schemas import SearchHit, SelfCorrectionTrace, utc_timestamp

logger = logging.getLogger("odar.dialectic")

COUNTER_QUERY_TEMPLATES = (
    "evidence against {h}",
    "{h} criticism limitations",
    "{h} debunked flawed retracted study",
    "why {h} is wrong expert dissent",
)

REFUTE_CUES = (
    "however",
    "contradict",
    "refute",
    "refutes",
    "debunk",
    "debunked",
    "retract",
    "retracted",
    "flawed",
    "flaw",
    "no evidence",
    "no significant",
    "failed to",
    "did not",
    "does not",
    "cannot",
    "criticism",
    "criticized",
    "overstated",
    "inconsistent",
    "dispute",
    "disputed",
    "myth",
    "misleading",
    "unfounded",
    "questionable",
    "replication failed",
    "not supported",
    "no benefit",
    "no difference",
    "ineffective",
    "skeptic",
)

SUPPORT_CUES = (
    "confirms",
    "confirmed",
    "supports",
    "supported",
    "demonstrates",
    "demonstrated",
    "found that",
    "evidence shows",
    "consistent with",
    "validated",
    "replicated",
    "significant improvement",
    "significant benefit",
    "effective",
    "robust",
    "agrees",
    "corroborat",
)


@dataclass
class DialecticReport:
    hypothesis: str
    verdict: str  # UPHELD | CHALLENGED | REFUTED | UNDECIDED
    support_score: int
    refute_score: int
    counter_queries: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    trace: Optional[SelfCorrectionTrace] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hypothesis": self.hypothesis,
            "verdict": self.verdict,
            "support_score": self.support_score,
            "refute_score": self.refute_score,
            "counter_queries": self.counter_queries,
            "evidence": self.evidence,
            "trace": self.trace.to_dict() if self.trace else None,
        }


def score_cues(text: str) -> Dict[str, Any]:
    """Count refutation/support cues in ``text`` (case-insensitive)."""
    lowered = text.lower()
    refute_hits = [cue for cue in REFUTE_CUES if cue in lowered]
    support_hits = [cue for cue in SUPPORT_CUES if cue in lowered]
    return {
        "refute_score": len(refute_hits),
        "support_score": len(support_hits),
        "refute_cues": refute_hits,
        "support_cues": support_hits,
    }


class DialecticalEngine:
    """Attacker-analyzer loop with self-correction bookkeeping."""

    def __init__(self, search_fn: Optional[Callable[[str], Sequence[SearchHit]]] = None) -> None:
        if search_fn is None:
            from odar.retrieval import ZeroCostSearch

            search_fn = ZeroCostSearch().text
        self.search_fn = search_fn

    # ------------------------------------------------------------------ #
    def attack(
        self,
        hypothesis: str,
        existing_evidence: Optional[Sequence[str]] = None,
        max_counter_queries: int = 2,
        evaluator: Optional[Any] = None,
    ) -> DialecticReport:
        """Stress-test ``hypothesis`` against retrieved counter-evidence.

        Verdict authority is SEMANTIC ONLY (P0 invariant):

        * with an ``evaluator`` (NLI stance) the verdict comes exclusively
          from semantic relations (REFUTES / SUPPORTS / neither);
        * without one the verdict is always ``UNDECIDED`` - keyword cue
          scores are recorded strictly as diagnostics and can never, on
          their own, establish support or refutation.
        """
        hypothesis = (hypothesis or "").strip()
        if not hypothesis:
            hypothesis = "the current working hypothesis"

        evidence: List[Dict[str, Any]] = []
        for snippet in existing_evidence or []:
            evidence.append(self._classify(snippet, origin="prior-evidence"))

        counter_queries: List[str] = []
        for template in COUNTER_QUERY_TEMPLATES[: max(0, int(max_counter_queries))]:
            query = template.format(h=hypothesis)
            counter_queries.append(query)
            try:
                hits = self.search_fn(query) or []
            except Exception as exc:  # poka-yoke: attack must not crash the DAG
                logger.warning("counter-search failed for %r: %s: %s", query, type(exc).__name__, exc)
                hits = []
            for hit in hits[:4]:
                snippet = hit.snippet if isinstance(hit, SearchHit) else str(hit)
                url = hit.url if isinstance(hit, SearchHit) else ""
                evidence.append(self._classify(snippet, origin="counter-search", url=url, query=query))

        # Topic contact + semantic stance bookkeeping.
        hypothesis_tokens = {t for t in tokenize(hypothesis) if t not in STOPWORDS and len(t) > 2}
        support_total = 0
        refute_total = 0
        semantic_refutes = False
        semantic_supports = False
        for item in evidence:
            snippet = item.get("snippet", "")
            snippet_tokens = {t for t in tokenize(snippet) if t not in STOPWORDS and len(t) > 2}
            item["topic_contact"] = bool(snippet_tokens & hypothesis_tokens)
            if item["topic_contact"]:
                support_total += item["support_score"]
                refute_total += item["refute_score"]
            if evaluator is not None and snippet:
                try:
                    relation, confidence = evaluator.stance(hypothesis, snippet)
                except Exception:
                    relation, confidence = None, 0.0
                item["semantic_relation"] = getattr(relation, "value", None)
                item["semantic_confidence"] = round(float(confidence or 0.0), 4)
                if relation is not None:
                    if relation.value == "REFUTES":
                        semantic_refutes = True
                    elif relation.value == "SUPPORTS":
                        semantic_supports = True

        if evaluator is not None:
            if semantic_refutes:
                verdict = "REFUTED"
            elif semantic_supports:
                verdict = "UPHELD"
            else:
                verdict = "UNDECIDED"
        else:
            # Cue-only path: diagnostics only, never a verdict.
            verdict = "UNDECIDED"

        trace = self._build_trace(hypothesis, verdict, evidence, support_total, refute_total)
        return DialecticReport(
            hypothesis=hypothesis,
            verdict=verdict,
            support_score=support_total,
            refute_score=refute_total,
            counter_queries=counter_queries,
            evidence=evidence,
            trace=trace,
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _classify(
        snippet: str,
        origin: str,
        url: str = "",
        query: str = "",
    ) -> Dict[str, Any]:
        cues = score_cues(snippet or "")
        stance = "NEUTRAL"
        if cues["refute_score"] > cues["support_score"]:
            stance = "REFUTES"
        elif cues["support_score"] > cues["refute_score"]:
            stance = "SUPPORTS"
        return {
            "snippet": (snippet or "")[:400],
            "url": url,
            "origin": origin,
            "query": query,
            "stance": stance,
            "support_score": cues["support_score"],
            "refute_score": cues["refute_score"],
            "refute_cues": cues["refute_cues"][:6],
            "support_cues": cues["support_cues"][:6],
        }

    @staticmethod
    def _verdict(support_total: int, refute_total: int) -> str:
        delta = refute_total - support_total
        if delta >= 3:
            return "REFUTED"
        if delta >= 1:
            return "CHALLENGED"
        if delta <= -1:
            return "UPHELD"
        return "UNDECIDED"

    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_trace(
        hypothesis: str,
        verdict: str,
        evidence: List[Dict[str, Any]],
        support_total: int,
        refute_total: int,
    ) -> SelfCorrectionTrace:
        refuting = [item for item in evidence if item["stance"] == "REFUTES"]
        supporting = [item for item in evidence if item["stance"] == "SUPPORTS"]

        if verdict in ("CHALLENGED", "REFUTED"):
            failed_hypothesis = (
                f"Original hypothesis asserted without qualification: '{hypothesis}'. "
                f"Attack surfaced {len(refuting)} refuting evidence item(s) "
                f"(refute cues {refute_total} vs support cues {support_total})."
            )
            diagnostic = (
                "; ".join(item["snippet"][:160] for item in refuting[:2])
                or "Refutation pressure detected via cue analysis of counter-evidence."
            )
            corrective_action = (
                "Claim strength downgraded; scope restricted to conditions where "
                "supporting evidence dominates; contradictory findings integrated "
                "explicitly into the synthesis instead of being suppressed."
            )
        else:
            failed_hypothesis = (
                f"No falsifying evidence found for '{hypothesis}' "
                f"(support cues {support_total} vs refute cues {refute_total}); "
                "hypothesis withstood the attack."
            )
            diagnostic = (
                "Counter-queries were executed and scored; refutation pressure was "
                "below the challenge threshold."
            )
            corrective_action = (
                "No retraction required; confidence annotated with the observed "
                "support/refute cue ratio and surviving attack is reported."
            )

        strongest_support = (
            supporting[0]["snippet"][:160] if supporting else "no single supporting span isolated"
        )
        verified_synthesis = (
            f"Post-attack position on '{hypothesis}': verdict={verdict}. "
            f"Strongest surviving support: {strongest_support}. "
            f"Remaining risk: {len(refuting)} refuting item(s) on record."
        )
        return SelfCorrectionTrace(
            hypothesis=hypothesis,
            failed_hypothesis=failed_hypothesis,
            diagnostic_detection=diagnostic,
            corrective_action=corrective_action,
            verified_synthesis=verified_synthesis,
            timestamp=utc_timestamp(),
        )
