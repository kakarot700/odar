"""REGRESSION (P1 FIX 12): synthesis is built strictly from the evidence
graph - no invented claims, no invented citations, no unsupported
certifications."""

import re

from odar.budget import Budget
from odar.citation_auditor import CitationAuditor, DeterministicNLIScorer
from odar.engine import ResearchEngine
from odar.final_audit import FinalAuditor
from odar.llm import ScriptedResearchController
from odar.schemas import ExtractedPage, SearchHit

FACTS = (
    "The Eiffel Tower is a wrought-iron lattice tower located on the Champ de Mars in Paris. "
    "Gustave Eiffel's company designed and built the tower, which was completed in 1889. "
    "The puddled iron structure is 330 metres tall and weighs about 10100 tonnes overall."
)


def lexical_auditor() -> CitationAuditor:
    auditor = CitationAuditor()
    auditor.scorer = DeterministicNLIScorer()
    auditor.scorer_backend = "injected"
    return auditor


class StubSearch:
    def text(self, query, max_results=6):
        return [SearchHit(url="https://example.org/tower", title="Tower", snippet=FACTS[:110], engine="stub")]


class StubExtractor:
    def extract(self, url):
        return ExtractedPage(
            url=url,
            ok=True,
            text=FACTS,
            title="Tower",
            chars=len(FACTS),
            engine="stub",
            resolved_url=url,
            http_status=200,
            content_hash="h",
            content_type="text/html",
        )


def run_once():
    engine = ResearchEngine(
        model=ScriptedResearchController(),
        search=StubSearch(),
        extractor=StubExtractor(),
        auditor=lexical_auditor(),
        budget=Budget(max_iterations=10, max_search_calls=3, max_fetches=2, max_wall_clock_s=30),
    )
    return engine.run("What is the Eiffel Tower made of and where is it located?")


class TestSynthesisGrounding:
    def test_every_citation_marker_resolves(self):
        outcome = run_once()
        markers = set(re.findall(r"\[((?:src|clm|ev)_[a-z0-9]+)\]", outcome.synthesis))
        assert markers, "synthesis should cite something"
        for marker in markers:
            resolved = (
                marker in outcome.state.sources
                or marker in outcome.state.claims
                or marker in outcome.state.evidence
            )
            assert resolved, f"invented citation: {marker}"

    def test_no_uncited_claim_in_certified_findings(self):
        outcome = run_once()
        for claim in outcome.state.supporting_claims():
            assert f"[{claim.claim_id}]" in outcome.synthesis
            assert "per [" in outcome.synthesis

    def test_no_findings_section_without_certified_claims(self):
        class EmptySearch:
            def text(self, query, max_results=6):
                return []

        engine = ResearchEngine(
            model=ScriptedResearchController(),
            search=EmptySearch(),
            auditor=lexical_auditor(),
            budget=Budget(max_iterations=4, max_search_calls=2, max_wall_clock_s=20),
        )
        outcome = engine.run("Anything at all?")
        assert "Certified findings" not in outcome.synthesis
        assert "No certified findings could be established" in outcome.synthesis
        assert outcome.state.uncertainty == "NO_EVIDENCE_FOUND"

    def test_unsupported_claims_never_certified_in_synthesis(self):
        outcome = run_once()
        certified_ids = {c.claim_id for c in outcome.state.supporting_claims()}
        provisional_ids = {c.claim_id for c in outcome.state.provisional_claims()}
        # Provisional claims live only in the provisional section.
        provisional_block = (
            outcome.synthesis.split("## Provisional Findings")[1]
            if "## Provisional Findings" in outcome.synthesis
            else ""
        )
        for cid in provisional_ids:
            assert cid in provisional_block
        for cid in provisional_ids:
            assert cid not in certified_ids

    def test_final_audit_passes_on_engine_synthesis(self):
        outcome = run_once()
        report = FinalAuditor().audit(outcome.state, outcome.synthesis)
        assert report.status in ("PASS", "PASS_WITH_FLAGS"), report.checks
        assert report.checks["no_unsupported_critical_claims"] is True
        assert report.checks["no_provisional_certifications"] is True

    def test_audit_flags_provisional_text_outside_labelled_section(self):
        # A provisional claim whose text leaks into the MAIN body while its
        # labelled section is removed must be flagged as unsupported.
        outcome = run_once()
        claim = next(c for c in outcome.state.claims.values() if c.status == "PROVISIONAL")
        stripped = outcome.synthesis.split("## Provisional Findings")[0]
        tampered = stripped + f"\n## Executive claims\n{claim.text} stands as fact.\n"
        report = FinalAuditor().audit(outcome.state, tampered)
        assert claim.claim_id in report.unsupported_claims
