"""REGRESSION (P1 FIX 11): claims are re-evaluated when materially new
evidence arrives.  "Has some evidence" must never mean "fully evaluated"."""

from odar.budget import Budget
from odar.citation_auditor import CitationAuditor
from odar.engine import ResearchEngine
from odar.evidence import Claim
from odar.llm import ScriptedResearchController
from odar.schemas import ExtractedPage, SearchHit


WEAK_TEXT = (
    "The topic of tower construction is discussed in general terms here. "
    "Various buildings exist in many cities around the world in general. "
    "This page mentions architecture but gives no specific factual details."
)
STRONG_TEXT = (
    "The Eiffel Tower is a wrought-iron lattice tower located on the Champ de Mars in Paris. "
    "Gustave Eiffel's company designed and built the tower, which was completed in 1889. "
    "The puddled iron structure is 330 metres tall and weighs about 10100 tonnes overall."
)
CONTRADICT_TEXT = (
    "The Eiffel Tower was never located on the Champ de Mars in Paris according to this source. "
    "The tower is not on the Champ de Mars and was not completed in 1889 per this account. "
    "The wrought-iron lattice tower claim is disputed by the authors of this page."
)


class StubNeuralVerifier:
    """Stand-in for an available neural NLI model (offline determinism)."""

    def __init__(self):
        from odar.citation_auditor import DeterministicNLIScorer

        self._impl = DeterministicNLIScorer()

    def predict(self, pairs):
        return self._impl.predict(pairs)


def neural_auditor() -> CitationAuditor:
    auditor = CitationAuditor()
    auditor.scorer = StubNeuralVerifier()
    auditor.scorer_backend = "stub-neural-verifier"
    return auditor


class TwoPhaseSearch:
    """Phase 1 returns the weak source; phase 2 adds the strong one."""

    def __init__(self):
        self.phase = 1

    def text(self, query, max_results=6):
        hits = [
            SearchHit(url="https://example.org/weak", title="Weak", snippet=WEAK_TEXT[:100], engine="stub")
        ]
        if self.phase >= 2:
            hits.append(
                SearchHit(
                    url="https://example.org/strong", title="Strong", snippet=STRONG_TEXT[:100], engine="stub"
                )
            )
        return hits


class TwoPhaseExtractor:
    def extract(self, url):
        body = WEAK_TEXT if "weak" in url else (STRONG_TEXT if "strong" in url else CONTRADICT_TEXT)
        return ExtractedPage(
            url=url,
            ok=True,
            text=body,
            title="T",
            chars=len(body),
            engine="stub",
            resolved_url=url,
            http_status=200,
            content_hash=url[-4:],
            content_type="text/html",
        )


class ContradictExtractor:
    def extract(self, url):
        body = CONTRADICT_TEXT if "contradict" in url else STRONG_TEXT
        return ExtractedPage(
            url=url,
            ok=True,
            text=body,
            title="T",
            chars=len(body),
            engine="stub",
            resolved_url=url,
            http_status=200,
            content_hash=url[-4:],
            content_type="text/html",
        )


class TestReevaluationOnNewEvidence:
    def test_weak_then_strong_evidence_upgrades_claim(self):
        search = TwoPhaseSearch()
        engine = ResearchEngine(
            model=ScriptedResearchController(max_fetches=2),
            search=search,
            extractor=TwoPhaseExtractor(),
            auditor=neural_auditor(),
            budget=Budget(
                max_iterations=16, max_search_calls=6, max_fetches=3, max_model_calls=40, max_wall_clock_s=60
            ),
        )
        # Phase 1: weak source only -> claims must stay uncertified.
        outcome1 = engine.run("What is the Eiffel Tower made of and where is it located?")
        assert outcome1.state.sources, "weak source should have been fetched"
        assert not outcome1.state.supporting_claims(), "weak evidence must not certify anything"
        weak_statuses = {c.status for c in outcome1.state.claims.values()}
        assert "CERTIFIED" not in weak_statuses

        # Phase 2: strong source arrives -> the SAME state is re-evaluated.
        search.phase = 2
        engine2 = ResearchEngine(
            model=ScriptedResearchController(max_fetches=2),
            search=search,
            extractor=TwoPhaseExtractor(),
            auditor=neural_auditor(),
            budget=Budget(
                max_iterations=16, max_search_calls=6, max_fetches=3, max_model_calls=40, max_wall_clock_s=60
            ),
        )
        outcome2 = engine2.run(
            "What is the Eiffel Tower made of and where is it located?",
            resume_state=outcome1.state,
        )
        assert outcome2.state.supporting_claims(), (
            "new strong evidence must trigger re-evaluation and upgrade the claim"
        )

    def test_new_source_marks_claims_stale(self):
        from odar.research_state import ResearchState

        state = ResearchState(objective="Q")
        claim = Claim(
            claim_id="clm_1",
            text="some claim",
            status="CERTIFIED",
            needs_evaluation=False,
            evaluated_sources=["src_old"],
        )
        state.add_claim(claim)
        state.mark_new_source("src_new")
        assert claim.needs_evaluation is True

    def test_already_evaluated_claim_not_reevaluated_without_new_sources(self):
        from odar.research_state import ResearchState
        from odar.evidence import EvidenceItem, Relation, SourceRecord
        from odar.schemas import new_id

        state = ResearchState(objective="Q")
        source = SourceRecord(source_id=new_id("src"), url="https://example.org/x", extracted_text="text")
        state.add_source(source)
        claim = Claim(
            claim_id=new_id("clm"),
            text="text",
            status="CERTIFIED",
            needs_evaluation=False,
            evaluated_sources=[source.source_id],
        )
        state.add_claim(claim)
        item = EvidenceItem(
            evidence_id=new_id("ev"),
            claim_id=claim.claim_id,
            source_id=source.source_id,
            span="text",
            relation=Relation.SUPPORTS,
            entailment_probability=0.9,
            contradiction_probability=0.05,
            relevance_score=1.0,
        )
        state.add_evidence(item)

        class NoNetSearch:
            def text(self, query, max_results=6):
                raise AssertionError("no network work expected")

        engine = ResearchEngine(
            model=ScriptedResearchController(),
            search=NoNetSearch(),
            auditor=neural_auditor(),
            budget=Budget(max_iterations=3, max_search_calls=1, max_fetches=1, max_wall_clock_s=20),
        )
        controller = engine.model
        decision = controller.decide(state, {})
        # Nothing new to evaluate and claims already supported -> synthesize,
        # never a redundant evaluation.
        assert decision.action != "evaluate_evidence"


class TestSupportThenContradiction:
    def test_later_contradiction_produces_conflict_not_silent_certification(self):
        class SupportThenContradictSearch:
            def __init__(self):
                self.phase = 1

            def text(self, query, max_results=6):
                hits = [
                    SearchHit(
                        url="https://example.org/strong", title="S", snippet=STRONG_TEXT[:100], engine="stub"
                    )
                ]
                if self.phase >= 2:
                    hits.append(
                        SearchHit(
                            url="https://example.org/contradict",
                            title="C",
                            snippet=CONTRADICT_TEXT[:100],
                            engine="stub",
                        )
                    )
                return hits

        search = SupportThenContradictSearch()
        engine = ResearchEngine(
            model=ScriptedResearchController(max_fetches=2),
            search=search,
            extractor=ContradictExtractor(),
            auditor=neural_auditor(),
            budget=Budget(
                max_iterations=16, max_search_calls=6, max_fetches=3, max_model_calls=40, max_wall_clock_s=60
            ),
        )
        first = engine.run("What is the Eiffel Tower made of and where is it located?")
        assert first.state.supporting_claims(), "supporting evidence should certify first"

        search.phase = 2
        engine2 = ResearchEngine(
            model=ScriptedResearchController(max_fetches=2),
            search=search,
            extractor=ContradictExtractor(),
            auditor=neural_auditor(),
            budget=Budget(
                max_iterations=16, max_search_calls=6, max_fetches=3, max_model_calls=40, max_wall_clock_s=60
            ),
        )
        second = engine2.run(
            "What is the Eiffel Tower made of and where is it located?",
            resume_state=first.state,
        )
        # The contradiction must be represented, not hidden behind the old
        # certification.  Depending on relative entailment strengths the claim
        # becomes CONFLICTING (mixed evidence) or REFUTED (contradiction wins);
        # either way it must NOT remain silently CERTIFIED.
        assert second.state.contradictions, "later contradiction must be recorded"
        statuses = {c.status for c in second.state.claims.values()}
        assert statuses & {"CONFLICTING", "REFUTED"}, statuses
        assert "CERTIFIED" not in statuses, "certification must not survive a semantic contradiction"
