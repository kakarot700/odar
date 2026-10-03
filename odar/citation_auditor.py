"""Decoupled claim & citation auditor.

Certifies claims *only* when they are entailed by retrieved source spans:

* Scoring backend: local CPU cross-encoder
  ``cross-encoder/nli-deberta-v3-small`` via ``sentence-transformers``
  (zero API cost).  If the model cannot be loaded (offline box, download
  failure), the auditor degrades to :class:`DeterministicNLIScorer`, a
  dependency-free lexical scorer with the same predict API - the pipeline
  therefore mistake-proofs itself instead of crashing.
* A claim is audited across **4-8 sentence spans** drawn from the cited
  sources; spans are ranked by lexical relevance to the claim.
* Certification rule: entailment probability must clear the **0.75**
  threshold (spec: > 0.75) *and* the span budget must be satisfied.

The scorer is injectable, which is also how the unit tests pin behaviour
deterministically.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, List, Optional, Sequence

from odar.evidence import (
    CIRCULARITY_THRESHOLD,
    Claim,
    EvidenceItem,
    Relation,
    SourceRecord,
    detect_circularity,
    provenance_complete,
)
from odar.loop_breaker import STOPWORDS, tokenize
from odar.schemas import ClaimAuditResult, Verdict, new_id

logger = logging.getLogger("odar.citation_auditor")

DEFAULT_MODEL_NAME = "cross-encoder/nli-deberta-v3-small"
DEFAULT_THRESHOLD = 0.75
MIN_SPANS = 4
MAX_SPANS = 8
CHUNK_SIZE = 300

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def softmax(row: Sequence[float]) -> List[float]:
    peak = max(row)
    exps = [math.exp(value - peak) for value in row]
    total = sum(exps)
    return [value / total for value in exps]


def _sentence_split(text: str) -> List[str]:
    parts = [part.strip() for part in _SENTENCE_RE.split(text or "")]
    return [part for part in parts if len(part.strip()) >= 3]


def _chunk(sentence: str, size: int = CHUNK_SIZE) -> List[str]:
    words = sentence.split()
    chunks: List[str] = []
    current: List[str] = []
    length = 0
    for word in words:
        if length + len(word) + 1 > size and current:
            chunks.append(" ".join(current))
            current = []
            length = 0
        current.append(word)
        length += len(word) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks


class DeterministicNLIScorer:
    """Lexical fallback scorer, API-compatible with ``CrossEncoder.predict``.

    Produces ``[contradiction, entailment, neutral]`` logit-style rows for a
    list of ``(premise, hypothesis)`` pairs.  Negation asymmetry between
    premise and hypothesis flips the verdict to contradiction; strong content
    overlap without negation asymmetry yields entailment; anything else is
    neutral.
    """

    NEGATORS = frozenset(
        {
            "not",
            "no",
            "never",
            "without",
            "cannot",
            "cant",
            "isnt",
            "arent",
            "wasnt",
            "werent",
            "false",
            "incorrect",
            "fails",
            "refutes",
            "contradicts",
            "lacks",
        }
    )

    # Antonym polarity groups: an antonym-swap between premise and hypothesis
    # (high overlap, opposing polarity) is contradiction, NOT entailment.
    _ANTONYM_GROUPS = [
        (
            frozenset({"improve", "improves", "improved", "improvement", "improving"}),
            frozenset({"worsen", "worsens", "worsened", "worsening"}),
        ),
        (
            frozenset({"help", "helps", "helped", "helpful", "benefit", "benefits", "beneficial"}),
            frozenset({"harm", "harms", "harmed", "harmful", "damage", "damages", "hurt", "hurts"}),
        ),
        (
            frozenset(
                {
                    "increase",
                    "increases",
                    "increased",
                    "increasing",
                    "higher",
                    "rise",
                    "rises",
                    "grew",
                    "grow",
                }
            ),
            frozenset(
                {
                    "decrease",
                    "decreases",
                    "decreased",
                    "decreasing",
                    "lower",
                    "fall",
                    "falls",
                    "decline",
                    "declines",
                    "shrink",
                    "shrinks",
                }
            ),
        ),
        (
            frozenset({"better", "faster", "stronger", "positive", "safe", "effective", "success"}),
            frozenset({"worse", "slower", "weaker", "negative", "dangerous", "ineffective", "failure"}),
        ),
        (
            frozenset(
                {"supports", "support", "confirms", "confirm", "validates", "validate", "proves", "prove"}
            ),
            frozenset(
                {
                    "refute",
                    "refutes",
                    "refuted",
                    "contradict",
                    "contradicts",
                    "disprove",
                    "disproves",
                    "undermine",
                    "undermines",
                }
            ),
        ),
        (
            frozenset({"recovered", "recover", "survived", "survive", "alive", "win", "wins", "won"}),
            frozenset({"died", "die", "dead", "fatal", "lose", "loses", "lost", "perished"}),
        ),
    ]

    def _polarity_side(self, tokens: set) -> Optional[int]:
        """0 = positive group, 1 = negative group, None = absent or mixed."""
        side = None
        for positive, negative in self._ANTONYM_GROUPS:
            if tokens & positive and tokens & negative:
                return None  # mixed polarity - cannot conclude
            if tokens & positive:
                if side is not None and side != 0:
                    return None
                side = 0
            if tokens & negative:
                if side is not None and side != 1:
                    return None
                side = 1
        return side

    _NEGATION_WINDOW = 4  # tokens around a negator that it can flip

    def _localized_negation(self, ordered_tokens: List[str], shared: set) -> bool:
        """True when a negator sits near a token SHARED with the other side.

        Distant negation (hedging clauses elsewhere in the sentence) does not
        flip polarity - only negation attached to the contested content does.
        """
        for idx, token in enumerate(ordered_tokens):
            if token in self.NEGATORS:
                window = ordered_tokens[max(0, idx - self._NEGATION_WINDOW) : idx + self._NEGATION_WINDOW + 1]
                if any(w in shared for w in window):
                    return True
        return False

    def predict(self, pairs: Sequence[Sequence[str]]) -> List[List[float]]:
        rows: List[List[float]] = []
        for premise, hypothesis in pairs:
            premise_ordered = tokenize(premise or "")
            premise_tokens = set(premise_ordered)
            claim_tokens = [t for t in tokenize(hypothesis or "") if t not in STOPWORDS]
            claim_set = set(claim_tokens) or set(tokenize(hypothesis or ""))
            overlap = len(premise_tokens & claim_set) / max(len(claim_set), 1)
            shared_content = (premise_tokens & claim_set) - STOPWORDS
            premise_negated = bool(premise_tokens & self.NEGATORS)
            claim_negated = bool(claim_set & self.NEGATORS)
            # Localized negation: a negator attached to shared content flips
            # polarity; hedge-clause negation elsewhere does not.
            localized_flip = len(shared_content) >= 3 and (
                (self._localized_negation(premise_ordered, shared_content) and not claim_negated)
                or (claim_negated and not premise_negated)
            )
            premise_side = self._polarity_side(premise_tokens)
            claim_side = self._polarity_side(claim_set)
            antonym_swap = premise_side is not None and claim_side is not None and premise_side != claim_side
            if (overlap >= 0.55 and (premise_negated != claim_negated or antonym_swap)) or localized_flip:
                rows.append([2.2, -1.5, 0.0])  # contradiction-dominant
            elif overlap >= 0.6:
                rows.append([-1.8, 1.0 + 2.2 * overlap, -0.4])  # entailment-dominant
            elif overlap >= 0.35:
                rows.append([0.0, 0.4, 1.2])  # neutral-leaning
            else:
                rows.append([-0.5, -0.8, 1.6])  # neutral
        return rows


class CitationAuditor:
    """NLI-based certification of claims against cited sources."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        threshold: float = DEFAULT_THRESHOLD,
        min_spans: int = MIN_SPANS,
        max_spans: int = MAX_SPANS,
        device: str = "cpu",
        scorer: Optional[Any] = None,
    ) -> None:
        self.model_name = model_name
        self.threshold = float(threshold)
        self.min_spans = int(min_spans)
        self.max_spans = int(max_spans)
        self.device = device
        self.scorer = scorer
        self.scorer_backend = "injected" if scorer is not None else "uninitialized"

    # ------------------------------------------------------------------ #
    # Scorer lifecycle
    # ------------------------------------------------------------------ #
    def _try_load_neural(self) -> Any:
        """Load the neural cross-encoder; raise on any failure."""
        from sentence_transformers import CrossEncoder

        return CrossEncoder(self.model_name, device=self.device)

    def ensure_scorer(self) -> None:
        if self.scorer is not None:
            return
        try:
            self.scorer = self._try_load_neural()
            self.scorer_backend = self.model_name
            logger.info("loaded NLI cross-encoder %s on %s", self.model_name, self.device)
        except Exception as exc:  # poka-yoke: degrade, never crash
            logger.warning(
                "cross-encoder unavailable (%s: %s); falling back to deterministic lexical scorer",
                type(exc).__name__,
                exc,
            )
            self.scorer = DeterministicNLIScorer()
            self.scorer_backend = "deterministic-lexical-fallback"

    # ------------------------------------------------------------------ #
    # Span preparation
    # ------------------------------------------------------------------ #
    def prepare_spans(self, text: str, claim: str = "") -> List[str]:
        """Split text into 4-8 ranked sentence spans when possible."""
        sentences = _sentence_split(text)
        if not sentences:
            return []
        spans: List[str] = []
        for sentence in sentences:
            if len(sentence) > CHUNK_SIZE:
                spans.extend(_chunk(sentence))
            else:
                spans.append(sentence)
        if len(spans) > self.max_spans:
            spans = self._rank_spans(spans, claim)[: self.max_spans]
        if len(spans) < self.min_spans:
            refined: List[str] = []
            for span in spans:
                pieces = re.split(r"(?<=[;:,])\s+|\s+(?:and|but|while|whereas)\s+", span)
                refined.extend(piece.strip() for piece in pieces if len(piece.strip()) >= 12)
            if len(refined) > len(spans):
                spans = refined[: max(self.max_spans, self.min_spans)]
        return spans[: self.max_spans]

    @staticmethod
    def _rank_spans(spans: List[str], claim: str) -> List[str]:
        claim_tokens = {t for t in tokenize(claim) if t not in STOPWORDS}
        if not claim_tokens:
            return spans
        scored = []
        for index, span in enumerate(spans):
            overlap = len(set(tokenize(span)) & claim_tokens)
            scored.append((-overlap, index, span))  # stable: relevance then position
        scored.sort(key=lambda item: (item[0], item[1]))
        return [span for _, _, span in scored]

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def audit(self, claim: str, sources: Sequence[str]) -> ClaimAuditResult:
        """Score ``claim`` against span pools built from ``sources``."""
        self.ensure_scorer()
        claim = (claim or "").strip()
        pool: List[str] = []
        for source in sources or []:
            pool.extend(self.prepare_spans(source, claim=claim))
        if not pool:
            return ClaimAuditResult(
                claim=claim,
                verdict=Verdict.NEUTRAL,
                entailment_probability=0.0,
                contradiction_probability=0.0,
                threshold=self.threshold,
                spans_checked=0,
                certified=False,
                scorer_backend=self.scorer_backend,
                note="no source spans available to audit against",
            )

        spans = self._rank_spans(pool, claim)[: self.max_spans] if len(pool) > self.max_spans else pool
        # Circularity bookkeeping: verbatim quotation of an EXTERNAL source is
        # legitimate direct citation.  Self-support (agent-generated text
        # cited as its own evidence) is blocked in the provenance-aware path
        # (:meth:`audit_with_provenance`).  Here we only *record* the score.
        circularity = [detect_circularity(claim, span) for span in spans]
        eligible = list(range(len(spans)))
        pairs = [(span, claim) for span in spans]
        try:
            raw_scores = self.scorer.predict(pairs)
        except Exception as exc:  # poka-yoke: model runtime failure degrades safely
            logger.warning("scorer failed (%s: %s); falling back to lexical scorer", type(exc).__name__, exc)
            self.scorer = DeterministicNLIScorer()
            self.scorer_backend = "deterministic-lexical-fallback"
            raw_scores = self.scorer.predict(pairs)

        probabilities = [softmax([float(value) for value in row]) for row in raw_scores]
        eligible_probs = [probabilities[i] for i in eligible]
        entailment_max = max(row[1] for row in eligible_probs)
        contradiction_max = max(row[0] for row in eligible_probs)
        entailment_mean = sum(row[1] for row in eligible_probs) / len(eligible_probs)

        if contradiction_max > entailment_max and contradiction_max >= 0.5:
            verdict = Verdict.CONTRADICTION
        elif entailment_max >= self.threshold:
            verdict = Verdict.ENTAILMENT
        else:
            verdict = Verdict.NEUTRAL

        span_budget_ok = self.min_spans <= len(spans) <= self.max_spans
        # Provisional == heuristic scorer.  Keyed on the scorer TYPE as well
        # as the backend label so no injected lexical scorer can escape the
        # "provisional can never certify" invariant.
        provisional = isinstance(self.scorer, DeterministicNLIScorer) or self.scorer_backend in (
            "deterministic-lexical-fallback",
        )
        certified = bool(
            verdict is Verdict.ENTAILMENT
            and entailment_max >= self.threshold
            and span_budget_ok
            and not provisional  # INVARIANT (defense layer 2)
        )
        note = ""
        if not span_budget_ok:
            note = f"span budget violated: {len(spans)} spans outside [{self.min_spans}, {self.max_spans}]"
        elif verdict is not Verdict.ENTAILMENT:
            note = f"verdict {verdict.value}: entailment probability {entailment_max:.3f} below threshold {self.threshold}"
        if provisional:
            prefix = "PROVISIONAL (heuristic scorer; neural model unavailable). "
            note = prefix + note if note else prefix.rstrip(". ") + ": treat verdict as heuristic"

        top_spans = sorted(eligible, key=lambda i: probabilities[i][1], reverse=True)[:3]
        evidence = [
            {
                "span": spans[i][:280],
                "span_hash": content_hash_short(spans[i]),
                "circularity": round(circularity[i], 4),
                "entailment_probability": round(probabilities[i][1], 4),
                "contradiction_probability": round(probabilities[i][0], 4),
                "neutral_probability": round(probabilities[i][2], 4),
            }
            for i in top_spans
        ]
        return ClaimAuditResult(
            claim=claim,
            verdict=verdict,
            entailment_probability=entailment_max,
            contradiction_probability=contradiction_max,
            threshold=self.threshold,
            spans_checked=len(spans),
            certified=certified,
            scorer_backend=self.scorer_backend,
            provisional=provisional,
            evidence=evidence,
            note=note or f"entailment mean across spans: {entailment_mean:.3f}",
        )

    # ------------------------------------------------------------------ #
    # Provenance-aware audit (production path)
    # ------------------------------------------------------------------ #
    @property
    def evaluator_id(self) -> str:
        provisional = isinstance(self.scorer, DeterministicNLIScorer) or self.scorer_backend in (
            "deterministic-lexical-fallback",
        )
        marker = "/PROVISIONAL" if provisional else ""
        return f"{self.scorer_backend}{marker}@enthr={self.threshold}/spans={self.min_spans}-{self.max_spans}"

    def audit_with_provenance(
        self,
        claim: Claim,
        sources: List[SourceRecord],
        source_texts: List[str],
    ) -> tuple:
        """Audit a claim and emit per-span :class:`EvidenceItem` records.

        Returns ``(ClaimAuditResult, List[EvidenceItem], provenance_gaps)``.
        Semantic relation assignment (never lexical overlap alone):

        * CIRCULAR     - near-duplicate span from an AGENT-ORIGIN source
                         (self-support; blocked)
        * SUPPORTS     - entailment probability >= threshold (verbatim
                         quotation of an external source counts as direct
                         citation, flagged via ``circularity_score``)
        * REFUTES      - contradiction probability dominates (>= 0.5)
        * QUALIFIES    - moderate entailment below threshold
        * INSUFFICIENT - low signal in either direction
        * IRRELEVANT   - negligible contact with the claim
        """
        self.ensure_scorer()
        result = self.audit(claim.text, source_texts)
        items: List[EvidenceItem] = []
        gaps: List[str] = []
        if not source_texts:
            gaps.append("no source texts supplied")
        pool: List[str] = []
        pool_source: List[int] = []
        for source_index, source_text in enumerate(source_texts):
            for span in self.prepare_spans(source_text, claim=claim.text):
                pool.append(span)
                pool_source.append(source_index)
        if len(pool) > self.max_spans:
            ranked = self._rank_spans(pool, claim.text)[: self.max_spans]
        else:
            ranked = list(pool)
        ranked_pool_index = [pool.index(span) for span in ranked]
        if ranked:
            pairs = [(span, claim.text) for span in ranked]
            try:
                raw = self.scorer.predict(pairs)
                probs = [softmax([float(v) for v in row]) for row in raw]
            except Exception as exc:
                logger.warning("provenance scoring failed: %s", exc)
                probs = [[0.0, 0.0, 1.0] for _ in ranked]
            claim_tokens = {t for t in tokenize(claim.text) if t not in STOPWORDS}
            for position, span in enumerate(ranked):
                source_index = pool_source[ranked_pool_index[position]]
                source = sources[source_index] if source_index < len(sources) else None
                circularity_score = detect_circularity(claim.text, span)
                p_con, p_ent, _neu = probs[position]
                overlap = len({t for t in tokenize(span) if t not in STOPWORDS} & claim_tokens)
                agent_origin = source is not None and source.origin != "external"
                if circularity_score >= CIRCULARITY_THRESHOLD and agent_origin:
                    relation = Relation.CIRCULAR  # self-support: blocked
                elif p_ent >= self.threshold:
                    relation = Relation.SUPPORTS
                elif p_con >= 0.5 and p_con > p_ent:
                    relation = Relation.REFUTES
                elif overlap == 0:
                    relation = Relation.IRRELEVANT
                elif p_ent >= self.threshold * 0.5:
                    relation = Relation.QUALIFIES
                else:
                    relation = Relation.INSUFFICIENT
                item = EvidenceItem(
                    evidence_id=new_id("ev"),
                    claim_id=claim.claim_id,
                    source_id=source.source_id if source else "unknown",
                    span=span,
                    relation=relation,
                    entailment_probability=p_ent,
                    contradiction_probability=p_con,
                    relevance_score=min(1.0, overlap / 6.0),
                    circularity_score=circularity_score,
                    evaluated_by=self.evaluator_id,
                )
                if source is not None:
                    gaps.extend(provenance_complete(source, item))
                else:
                    gaps.append(f"evidence {item.evidence_id} has no source record")
                items.append(item)
        return result, items, sorted(set(gaps))


def content_hash_short(text: str) -> str:
    from odar.evidence import content_hash as _content_hash

    return _content_hash(text)[:16]
