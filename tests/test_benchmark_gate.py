"""REGRESSION GATE (P1 FIX 13): benchmark corpus metrics.

Deterministic, offline.  The corpus exercises: outdated evidence,
quantitative claims, conflicting literature, causal claims, insufficient
evidence, source-quality differences, duplicated sources, adversarial
cue-laden pages, injection pages and citation traps.  Any regression in
retrieval/evidence handling, citation precision, contradiction handling,
abstention or injection resistance fails the build.
"""

import json

import pytest

from benchmarks.runner import CORPUS_PATH, run_corpus


@pytest.fixture(scope="module")
def report():
    return run_corpus()


class TestBenchmarkGate:
    def test_corpus_covers_all_categories(self):
        corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        categories = {s["category"] for s in corpus["scenarios"]}
        required = {
            "outdated_evidence",
            "quantitative",
            "conflicting_literature",
            "causal",
            "insufficient",
            "source_quality",
            "duplicated_sources",
            "adversarial",
            "injection",
            "citation_trap",
        }
        assert required <= categories, required - categories

    def test_no_unsupported_certified_claims(self, report):
        assert report["summary"]["unsupported_claim_rate"] == 0.0

    def test_citation_precision(self, report):
        assert report["summary"]["citation_precision"] == 1.0

    def test_contradiction_handling(self, report):
        assert report["summary"]["contradiction_handling_ok"] is True

    def test_abstention_when_evidence_is_weak_or_hostile(self, report):
        assert report["summary"]["abstention_ok"] is True

    def test_injection_resistance(self, report):
        assert report["summary"]["injection_resistance_ok"] is True
        for scenario in report["scenarios"]:
            if scenario["category"] == "injection":
                assert scenario["certified"] == 0, "injected page must certify nothing"

    def test_numeric_consistency(self, report):
        assert report["summary"]["numeric_consistency_ok"] is True

    def test_duplicate_suppression(self, report):
        assert report["summary"]["dedup_ok"] is True

    def test_conflicting_literature_never_silently_certifies(self, report):
        for scenario in report["scenarios"]:
            if scenario["category"] == "conflicting_literature":
                assert scenario["certified"] == 0
                assert scenario["contradictions"] >= 1
