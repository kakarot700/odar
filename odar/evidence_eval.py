"""Semantic evidence stance evaluation.

Keyword-cue stance classification is trivially manipulable ("the study
supports X" flips a verdict regardless of what the study says).  Production
stance evaluation is NLI-based: the evidence span is the *premise*, the
hypothesis/claim is the *hypothesis*, and the cross-encoder decides
entailment vs contradiction semantically.

The lexical cue scorer survives only as an explicitly-labelled fast heuristic
for telemetry, never as the arbiter of support/refute in the certification
path.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from odar.citation_auditor import CitationAuditor, softmax
from odar.evidence import Relation
from odar.loop_breaker import STOPWORDS, tokenize


class SemanticStanceEvaluator:
    """NLI-backed stance classifier for evidence spans vs hypotheses."""

    SUPPORT_THRESHOLD = 0.5
    REFUTE_THRESHOLD = 0.5
    QUALIFY_THRESHOLD = 0.3

    def __init__(self, auditor: Optional[CitationAuditor] = None) -> None:
        self.auditor = auditor or CitationAuditor()
        self.auditor.ensure_scorer()

    # ------------------------------------------------------------------ #
    def stance(self, hypothesis: str, snippet: str) -> Tuple[Relation, float]:
        """Return ``(relation, confidence)`` for one snippet/hypothesis pair.

        Semantic rules:
        * contradiction-dominant (p_con >= 0.5 and > p_ent) -> REFUTES
        * entailment-dominant (p_ent >= 0.5)                 -> SUPPORTS
        * meaningful signal either way (>= 0.3)              -> QUALIFIES
        * negligible semantic contact                        -> IRRELEVANT
        """
        hypothesis = (hypothesis or "").strip()
        snippet = (snippet or "").strip()
        if not hypothesis or not snippet:
            return Relation.INSUFFICIENT, 0.0
        try:
            raw = self.auditor.scorer.predict([(snippet, hypothesis)])
            probs = softmax([float(v) for v in raw[0]])
        except Exception:
            return Relation.INSUFFICIENT, 0.0
        p_con, p_ent, p_neu = probs
        overlap = len(
            {t for t in tokenize(snippet) if t not in STOPWORDS}
            & {t for t in tokenize(hypothesis) if t not in STOPWORDS}
        )
        if p_con >= self.REFUTE_THRESHOLD and p_con > p_ent:
            return Relation.REFUTES, p_con
        if p_ent >= self.SUPPORT_THRESHOLD:
            return Relation.SUPPORTS, p_ent
        if max(p_ent, p_con) >= self.QUALIFY_THRESHOLD:
            return Relation.QUALIFIES, max(p_ent, p_con)
        if overlap == 0:
            return Relation.IRRELEVANT, p_neu
        return Relation.INSUFFICIENT, p_neu

    # ------------------------------------------------------------------ #
    def classify_batch(self, hypothesis: str, snippets: List[str]) -> List[Tuple[Relation, float]]:
        return [self.stance(hypothesis, snippet) for snippet in snippets]
