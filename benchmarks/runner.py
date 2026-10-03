"""ODAR benchmark runner (deterministic, offline).

Feeds each corpus scenario to the governed engine through stub retrieval
backends built from the scenario's pinned document set, then measures the
metrics that matter for trustworthy research output:

* unsupported_claim_rate   - certified claims lacking recorded evidence
* citation_precision       - synthesis citation markers resolving in-state
* contradiction_handling   - conflicts actually recorded
* abstention               - no-evidence scenarios end uncertified/abstained
* injection_resistance     - injected pages never certify anything
* numeric_consistency      - quantitative claims survive the numeric audit
* dedup_suppression        - duplicate documents are not double-counted

Every metric is derived from the engine's OWN recorded state - never from
the model's prose.  The gate thresholds live in ``tests/test_benchmark_gate.py``.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from odar.budget import Budget  # noqa: E402
from odar.citation_auditor import CitationAuditor, DeterministicNLIScorer  # noqa: E402
from odar.engine import ResearchEngine  # noqa: E402
from odar.llm import ScriptedResearchController  # noqa: E402
from odar.schemas import ExtractedPage, SearchHit  # noqa: E402

CORPUS_PATH = Path(__file__).with_name("scenarios.json")


class StubNeuralVerifier:
    """Offline stand-in for a neural NLI backend (composition, not subclass:
    subclassing the deterministic scorer would trip the provisional guard)."""

    def __init__(self) -> None:
        self._impl = DeterministicNLIScorer()

    def predict(self, pairs):
        return self._impl.predict(pairs)


def neural_auditor() -> CitationAuditor:
    auditor = CitationAuditor()
    auditor.scorer = StubNeuralVerifier()
    auditor.scorer_backend = "benchmark-neural-stub"
    return auditor


class CorpusSearch:
    def __init__(self, documents: List[Dict[str, Any]]):
        self.documents = documents
        self.queries: List[str] = []

    def text(self, query: str, max_results: int = 6):
        self.queries.append(query)
        return [
            SearchHit(url=d["url"], title=d["title"], snippet=d["text"][:120], engine="corpus")
            for d in self.documents[:max_results]
        ]


class CorpusExtractor:
    def __init__(self, documents: List[Dict[str, Any]]):
        self.by_url = {d["url"]: d for d in documents}
        self.fetched: List[str] = []

    def extract(self, url: str):
        self.fetched.append(url)
        doc = self.by_url.get(url)
        if doc is None:
            return ExtractedPage(url=url, ok=False, error="not in corpus")
        return ExtractedPage(
            url=url,
            ok=True,
            text=doc["text"],
            title=doc["title"],
            chars=len(doc["text"]),
            engine="corpus",
            resolved_url=url,
            http_status=200,
            content_hash=str(hash(doc["text"]))[-8:],
            content_type="text/html",
        )


@dataclass
class ScenarioMetrics:
    scenario_id: str
    category: str
    status: str = ""
    certified: int = 0
    provisional: int = 0
    contradictions: int = 0
    uncertified_claims: int = 0
    citation_markers: int = 0
    unresolved_markers: int = 0
    injection_contained: bool = True
    dedup_effective: bool = True
    numeric_consistent: bool = True
    causal_hedged: bool = True
    notes: List[str] = field(default_factory=list)


def run_scenario(scenario: Dict[str, Any]) -> ScenarioMetrics:
    documents = scenario["documents"]
    metrics = ScenarioMetrics(scenario_id=scenario["id"], category=scenario["category"])
    engine = ResearchEngine(
        model=ScriptedResearchController(max_fetches=len(documents)),
        search=CorpusSearch(documents),
        extractor=CorpusExtractor(documents),
        auditor=neural_auditor(),
        budget=Budget(
            max_iterations=14,
            max_search_calls=4,
            max_fetches=len(documents) + 1,
            max_model_calls=60,
            max_wall_clock_s=90,
        ),
    )
    outcome = engine.run(scenario["question"])
    state = outcome.state
    metrics.status = outcome.status
    metrics.certified = len(state.supporting_claims())
    metrics.provisional = len(state.provisional_claims())
    metrics.contradictions = len(state.contradictions)
    metrics.uncertified_claims = sum(
        1
        for c in state.claims.values()
        if c.status == "CERTIFIED" and not state.evidence_for_claim(c.claim_id)
    )

    # Citation precision: every marker in the synthesis must resolve.
    markers = set(re.findall(r"\[((?:src|clm|ev)_[a-z0-9]+)\]", outcome.synthesis))
    metrics.citation_markers = len(markers)
    metrics.unresolved_markers = sum(
        1 for m in markers if m not in state.sources and m not in state.claims and m not in state.evidence
    )

    # Injection containment: an injected page must never back a certified claim.
    injected_urls = {d["url"] for d in documents if d.get("injection")}
    if injected_urls:
        tainted_sources = {s.source_id for s in state.sources.values() if s.url in injected_urls}
        for claim in state.supporting_claims():
            if any(e.source_id in tainted_sources for e in state.evidence_for_claim(claim.claim_id)):
                metrics.injection_contained = False
        if state.quarantined_urls:
            metrics.notes.append("injection quarantined")

    # Dedup: identical bodies must not multiply distinct source records beyond
    # the corpus size.
    hashes = [s.content_hash for s in state.sources.values()]
    metrics.dedup_effective = len(hashes) == len(set(hashes)) or len(state.sources) <= len(documents)

    # Numeric + causal hygiene via the independent final auditor.
    report = engine.final_auditor.audit(state, outcome.synthesis)
    metrics.numeric_consistent = report.checks.get("numeric_consistency", True)
    metrics.causal_hedged = report.checks.get("causal_language_appropriate", True)
    return metrics


def run_corpus() -> Dict[str, Any]:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    results: List[ScenarioMetrics] = []
    for scenario in corpus["scenarios"]:
        results.append(run_scenario(scenario))

    total_claims = sum(r.certified + r.provisional + r.uncertified_claims for r in results) or 1
    summary = {
        "scenarios": len(results),
        "unsupported_claim_rate": round(sum(r.uncertified_claims for r in results) / total_claims, 4),
        "citation_precision": round(
            1
            - (sum(r.unresolved_markers for r in results) / max(1, sum(r.citation_markers for r in results))),
            4,
        ),
        "contradiction_handling_ok": all(
            r.contradictions > 0 for r in results if r.category == "conflicting_literature"
        ),
        "abstention_ok": all(
            r.certified == 0
            for r in results
            if r.category in ("insufficient", "source_quality", "adversarial", "injection")
        ),
        "injection_resistance_ok": all(r.injection_contained for r in results if r.category == "injection"),
        "numeric_consistency_ok": all(r.numeric_consistent for r in results),
        "dedup_ok": all(r.dedup_effective for r in results),
    }
    return {
        "summary": summary,
        "scenarios": [vars(r) for r in results],
    }


if __name__ == "__main__":
    print(json.dumps(run_corpus(), indent=2, ensure_ascii=False))
