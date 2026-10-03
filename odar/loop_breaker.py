"""In-flight semantic loop-breaker for search queries.

Poka-yoke guard against degenerate retrieval loops:

* A sliding window of the most recent queries is kept.
* Every outgoing query is compared to the window using character-trigram
  cosine similarity.  If similarity exceeds ``similarity_threshold``
  (default **0.82**) the query is *intercepted* and a structurally different
  alternative search vector is forced instead (facet rotation), so the agent
  cannot hammer the same retrieval surface twice.
* A hard Jaccard deduplication gate (default **0.85**) outright blocks
  near-identical token sets that were already issued earlier in the session
  (fires after the reroute gate, so an immediate repeat is intercepted and
  rerouted first, while stale re-issues are blocked).

The similarity backends are dependency-free (character trigrams + token
Jaccard), which keeps the breaker usable on any CPU with zero install cost.
"""

from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

_TOKEN_RE = re.compile(r"[a-z0-9']+")

STOPWORDS = frozenset(
    """a an the of on in for to and or does do did is are was were be been being
    what which who whom how why when where versus vs about with without than
    then this that these those it its into over under by at from as""".split()
)

# Action labels ----------------------------------------------------------------
ACTION_PASS = "pass"
ACTION_REROUTE = "reroute"
ACTION_BLOCK = "block"


def tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokenization."""
    return _TOKEN_RE.findall(text.lower())


def char_ngrams(text: str, n: int = 3) -> Counter:
    """Character n-gram multiset of a normalized string."""
    normalized = re.sub(r"\s+", " ", text.lower().strip())
    padded = f" {normalized} "
    if len(padded) < n:
        return Counter({padded: 1})
    return Counter(padded[i : i + n] for i in range(len(padded) - n + 1))


def cosine_similarity(a: str, b: str) -> float:
    """Character-trigram cosine similarity in [0, 1]."""
    grams_a = char_ngrams(a)
    grams_b = char_ngrams(b)
    if not grams_a or not grams_b:
        return 0.0
    common = set(grams_a) & set(grams_b)
    dot = sum(grams_a[g] * grams_b[g] for g in common)
    norm_a = sum(v * v for v in grams_a.values()) ** 0.5
    norm_b = sum(v * v for v in grams_b.values()) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def jaccard_similarity(a: str, b: str) -> float:
    """Token-set Jaccard similarity in [0, 1]."""
    set_a = set(tokenize(a))
    set_b = set(tokenize(b))
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


@dataclass
class LoopBreakerDecision:
    """Outcome of guarding one query."""

    action: str
    original_query: str
    query: str  # the query that should actually be executed
    similarity: float  # best cosine similarity against the sliding window
    jaccard: float  # best jaccard similarity against full history
    reason: str
    strategy: Optional[str] = None

    @property
    def intercepted(self) -> bool:
        return self.action in (ACTION_REROUTE, ACTION_BLOCK)

    def to_dict(self) -> Dict[str, object]:
        return {
            "action": self.action,
            "original_query": self.original_query,
            "query": self.query,
            "similarity": round(self.similarity, 4),
            "jaccard": round(self.jaccard, 4),
            "reason": self.reason,
            "strategy": self.strategy,
        }


# Alternative search vectors ---------------------------------------------------
# The key is rebuilt from the leading content tokens, then re-projected
# through a rotating bank of orthogonal research facets (spec facets:
# statistics, criticism, systematic review, dispute, case study).  This
# guarantees the replacement query is structurally different (low trigram
# overlap) instead of a cosmetic rewording.
ALTERNATE_VECTORS: Tuple[Tuple[str, str], ...] = (
    ("statistics", "{key} statistics measurements quantitative data"),
    ("criticism", "{key} criticism limitations counterarguments rebuttal"),
    ("systematic_review", "{key} systematic review meta-analysis findings"),
    ("dispute", "why {key} disputed debate expert disagreement"),
    ("case_study", "{key} case study real world results outcomes"),
)


class SemanticLoopBreaker:
    """Sliding-window semantic guard for retrieval queries."""

    def __init__(
        self,
        window_size: int = 5,
        similarity_threshold: float = 0.82,
        jaccard_threshold: float = 0.85,
    ) -> None:
        self.window_size = window_size
        self.similarity_threshold = similarity_threshold
        self.jaccard_threshold = jaccard_threshold
        self._window: Deque[str] = deque(maxlen=window_size)
        self._history: List[str] = []
        self._reroute_counter = 0
        self.stats: Dict[str, int] = {"passed": 0, "rerouted": 0, "blocked": 0}

    # -- public API -----------------------------------------------------------
    def guard(self, query: str) -> LoopBreakerDecision:
        """Evaluate ``query`` and decide pass / reroute / block."""
        normalized = " ".join(query.split())
        if not normalized:
            return LoopBreakerDecision(
                action=ACTION_BLOCK,
                original_query=query,
                query="",
                similarity=0.0,
                jaccard=0.0,
                reason="empty query rejected (poka-yoke)",
            )

        best_cosine = max(
            (cosine_similarity(normalized, prev) for prev in self._window),
            default=0.0,
        )
        best_jaccard = max(
            (jaccard_similarity(normalized, prev) for prev in self._history),
            default=0.0,
        )

        # Gate 1: in-flight semantic intercept (cosine over the sliding window).
        # Consecutive near-identical queries are rerouted to an alternate
        # search vector rather than dropped, preserving research momentum.
        if best_cosine >= self.similarity_threshold:
            replacement, strategy = self._alternate_vector(normalized)
            self._reroute_counter += 1
            self.stats["rerouted"] += 1
            self._record(replacement)
            return LoopBreakerDecision(
                action=ACTION_REROUTE,
                original_query=query,
                query=replacement,
                similarity=best_cosine,
                jaccard=best_jaccard,
                reason=(
                    f"cosine similarity {best_cosine:.3f} >= "
                    f"{self.similarity_threshold:.2f}: forced alternate search vector"
                ),
                strategy=strategy,
            )

        # Gate 2: hard Jaccard deduplication circuit breaker over the full
        # session history - a stale near-duplicate is blocked outright.
        if best_jaccard >= self.jaccard_threshold:
            self.stats["blocked"] += 1
            return LoopBreakerDecision(
                action=ACTION_BLOCK,
                original_query=query,
                query=normalized,
                similarity=best_cosine,
                jaccard=best_jaccard,
                reason=(
                    f"jaccard similarity {best_jaccard:.3f} >= "
                    f"{self.jaccard_threshold:.2f}: near-duplicate query blocked"
                ),
            )

        self.stats["passed"] += 1
        self._record(normalized)
        return LoopBreakerDecision(
            action=ACTION_PASS,
            original_query=query,
            query=normalized,
            similarity=best_cosine,
            jaccard=best_jaccard,
            reason="within similarity budget",
        )

    def reset(self) -> None:
        self._window.clear()
        self._history.clear()
        self._reroute_counter = 0
        self.stats = {"passed": 0, "rerouted": 0, "blocked": 0}

    # -- internals ------------------------------------------------------------
    def _record(self, query: str) -> None:
        self._window.append(query)
        self._history.append(query)

    def _alternate_vector(self, query: str) -> Tuple[str, str]:
        tokens = [t for t in tokenize(query) if t not in STOPWORDS and len(t) > 2]
        key_tokens: List[str] = []
        for token in tokens:  # original order preserves the topical anchor
            if token not in key_tokens:
                key_tokens.append(token)
            if len(key_tokens) == 4:
                break
        key = " ".join(key_tokens) if key_tokens else "topic evidence findings"
        strategy, template = ALTERNATE_VECTORS[self._reroute_counter % len(ALTERNATE_VECTORS)]
        return template.format(key=key), strategy
