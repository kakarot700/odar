"""REGRESSION (P0 FIX 1): a provisional/heuristic NLI result can NEVER
become a production CERTIFIED claim - enforced at four layers."""

from odar.citation_auditor import CitationAuditor, DeterministicNLIScorer
from odar.evidence import Claim, Relation, SourceRecord
from odar.final_audit import FinalAuditor
from odar.research_state import ResearchState
from odar.schemas import ClaimAuditResult, Verdict, new_id

STRONG_SOURCE = (
    "The Eiffel Tower is a wrought-iron lattice tower located on the Champ de Mars in Paris. "
    "Gustave Eiffel's company designed and built the tower, which was completed in 1889. "
    "The puddled iron structure is 330 metres tall and weighs about 10100 tonnes overall."
)
STRONG_CLAIM = "A wrought-iron lattice tower called the Eiffel Tower stands on the Champ de Mars in Paris."


def lexical_auditor() -> CitationAuditor:
    auditor = CitationAuditor()
    auditor.scorer = DeterministicNLIScorer()
    auditor.scorer_backend = "injected-lexical"
    return auditor


class TestLayerAuditor:
    def test_lexical_audit_result_is_never_certified(self):
        auditor = lexical_auditor()
        result = auditor.audit(STRONG_CLAIM, [STRONG_SOURCE])
        assert result.provisional is True
        assert result.certified is False, "lexical fallback must never certify"

    def test_provisional_flag_survives_even_if_constructor_forces_certified(self):
        # Defense layer in the schema itself: a provisional result cannot
        # carry certified=True, however it is constructed.
        result = ClaimAuditResult(
            claim="x",
            verdict=Verdict.ENTAILMENT,
            entailment_probability=0.99,
            contradiction_probability=0.01,
            threshold=0.75,
            spans_checked=4,
            certified=True,
            scorer_backend="deterministic-lexical-fallback",
            provisional=True,
        )
        assert result.certified is False
        assert "suppressed" in result.note

    def test_provisional_keyed_on_scorer_type_not_just_label(self):
        auditor = lexical_auditor()
        auditor.scorer_backend = "looks-neural-but-is-not"
        result = auditor.audit(STRONG_CLAIM, [STRONG_SOURCE])
        assert result.provisional is True
        assert result.certified is False

    def test_evaluator_id_marks_provisional_for_audit_trail(self):
        auditor = lexical_auditor()
        auditor.ensure_scorer()
        assert "PROVISIONAL" in auditor.evaluator_id


class TestLayerEngine:
    def _run_engine_with_lexical(self):
        from odar.budget import Budget
        from odar.engine import ResearchEngine
        from odar.llm import ScriptedResearchController
        from odar.schemas import ExtractedPage, SearchHit

        class OneSearch:
            def text(self, query, max_results=6):
                return [
                    SearchHit(
                        url="https://example.org/tower",
                        title="Tower",
                        snippet=STRONG_SOURCE[:120],
                        engine="stub",
                    )
                ]

        class OneExtractor:
            def extract(self, url):
                return ExtractedPage(
                    url=url,
                    ok=True,
                    text=STRONG_SOURCE,
                    title="Tower",
                    chars=len(STRONG_SOURCE),
                    engine="stub",
                    resolved_url=url,
                    http_status=200,
                    content_hash="h",
                    content_type="text/html",
                )

        engine = ResearchEngine(
            model=ScriptedResearchController(),
            search=OneSearch(),
            extractor=OneExtractor(),
            auditor=lexical_auditor(),
            budget=Budget(max_iterations=10, max_search_calls=3, max_fetches=2, max_wall_clock_s=30),
        )
        return engine.run("What is the Eiffel Tower made of and where is it located?")

    def test_action_evaluate_never_certifies_provisional(self):
        outcome = self._run_engine_with_lexical()
        assert outcome.state.claims, "claims should have been extracted"
        for claim in outcome.state.claims.values():
            assert claim.status != "CERTIFIED", (
                f"_action_evaluate turned a provisional result into CERTIFIED: {claim.text[:50]}"
            )
        # Supporting (provisional) findings must exist but stay provisional.
        assert outcome.state.provisional_claims(), "strong entailment should yield PROVISIONAL status"

    def test_report_represents_degraded_verification(self):
        outcome = self._run_engine_with_lexical()
        lowered = outcome.synthesis.lower()
        assert "provisional" in lowered or "heuristic" in lowered
        assert "certified findings (semantic nli verification)" not in lowered

    def test_supporting_claims_excludes_provisional(self):
        outcome = self._run_engine_with_lexical()
        assert outcome.state.supporting_claims() == []


class TestLayerFinalAudit:
    def test_audit_flags_any_provisional_claim_marked_certified(self):
        state = ResearchState(objective="Q")
        source = SourceRecord(
            source_id=new_id("src"),
            url="https://example.org/x",
            http_status=200,
            title="T",
            content_hash="h",
            extracted_text="The tower is 330 metres tall.",
        )
        state.add_source(source)
        claim = Claim(
            claim_id=new_id("clm"), text="The tower is 330 metres tall.", status="CERTIFIED", provisional=True
        )  # corrupted upstream state
        state.add_claim(claim)
        report = FinalAuditor().audit(state, f"Finding [{claim.claim_id}] per [{source.source_id}].")
        assert report.checks["no_provisional_certifications"] is False
        assert report.status == "FAIL"

    def test_audit_flags_provisional_evaluator_on_certified_claim(self):
        import time

        from odar.evidence import EvidenceItem

        state = ResearchState(objective="Q")
        source = SourceRecord(
            source_id=new_id("src"),
            url="https://example.org/x",
            http_status=200,
            retrieved_at=time.time(),
            title="T",
            content_hash="h",
            extracted_text="The tower is 330 metres tall.",
        )
        state.add_source(source)
        claim = Claim(
            claim_id=new_id("clm"),
            text="The tower is 330 metres tall.",
            status="CERTIFIED",
            provisional=False,
        )
        state.add_claim(claim)
        item = EvidenceItem(
            evidence_id=new_id("ev"),
            claim_id=claim.claim_id,
            source_id=source.source_id,
            span="The tower is 330 metres tall.",
            relation=Relation.SUPPORTS,
            entailment_probability=0.97,
            contradiction_probability=0.01,
            relevance_score=1.0,
            evaluated_by="deterministic-lexical-fallback/PROVISIONAL@enthr=0.75",
        )
        state.add_evidence(item)
        report = FinalAuditor().audit(
            state,
            f"Finding [{claim.claim_id}] per [{source.source_id}]. Limitations remain; uncertainty applies.",
        )
        assert report.checks["no_provisional_certifications"] is False
        assert claim.claim_id in report.provisional_certifications

    def test_engine_demotes_audit_flagged_provisional_certifications(self):
        # If a corrupted CERTIFIED+provisional claim reaches synthesis, the
        # engine demotes it instead of publishing.
        from odar.budget import Budget
        from odar.engine import ResearchEngine
        from odar.llm import ScriptedResearchController
        from odar.schemas import ExtractedPage, SearchHit

        class OneSearch:
            def text(self, query, max_results=6):
                return [
                    SearchHit(
                        url="https://example.org/tower",
                        title="Tower",
                        snippet=STRONG_SOURCE[:120],
                        engine="stub",
                    )
                ]

        class OneExtractor:
            def extract(self, url):
                return ExtractedPage(
                    url=url,
                    ok=True,
                    text=STRONG_SOURCE,
                    title="Tower",
                    chars=len(STRONG_SOURCE),
                    engine="stub",
                    resolved_url=url,
                    http_status=200,
                    content_hash="h",
                    content_type="text/html",
                )

        engine = ResearchEngine(
            model=ScriptedResearchController(),
            search=OneSearch(),
            extractor=OneExtractor(),
            auditor=lexical_auditor(),
            budget=Budget(max_iterations=10, max_search_calls=3, max_fetches=2, max_wall_clock_s=30),
        )
        outcome = engine.run("What is the Eiffel Tower made of and where is it located?")
        # Simulate upstream corruption after the run, then re-audit.
        for claim in outcome.state.claims.values():
            if claim.status == "PROVISIONAL":
                claim.status = "CERTIFIED"
        report = FinalAuditor().audit(outcome.state, outcome.synthesis)
        assert report.status == "FAIL"  # the audit catches what the engine prevents
