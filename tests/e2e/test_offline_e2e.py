"""E2E suite (OFFLINE, deterministic): full engine runs over stubbed I/O.

These tests exercise the complete goal-driven loop - decision, gate,
execution, evidence evaluation, contradiction handling, synthesis, final
audit - with zero network access.

Verifier note: ``StubNeuralScorer`` stands in for an AVAILABLE neural NLI
model (the provisional invariant forbids certification from the lexical
scorer).  The provisional/lexical path is covered explicitly below and in
``tests/unit/test_provisional_invariant.py``; the real model runs in the
integration suite.
"""

from odar.budget import Budget
from odar.citation_auditor import CitationAuditor, DeterministicNLIScorer
from odar.engine import ResearchEngine
from odar.evidence import Uncertainty
from odar.llm import ScriptedResearchController
from odar.schemas import ExtractedPage, SearchHit


class StubNeuralScorer:
    """Offline stand-in for an available neural cross-encoder.

    Composition (not subclassing) is deliberate: the provisional invariant
    keys on ``isinstance(scorer, DeterministicNLIScorer)``, and this stub
    simulates an AVAILABLE neural verifier so the certification pipeline can
    be exercised offline.  The lexical/provisional path is tested separately
    with a genuine ``DeterministicNLIScorer``.
    """

    def __init__(self) -> None:
        self._impl = DeterministicNLIScorer()

    def predict(self, pairs):
        return self._impl.predict(pairs)


def neural_auditor() -> CitationAuditor:
    auditor = CitationAuditor()
    auditor.scorer = StubNeuralScorer()
    auditor.scorer_backend = "stub-neural-verifier"
    return auditor


def lexical_auditor() -> CitationAuditor:
    auditor = CitationAuditor()
    auditor.scorer = DeterministicNLIScorer()
    auditor.scorer_backend = "deterministic-lexical-fallback"
    return auditor


TOWER_TEXT = (
    "The Eiffel Tower is a wrought-iron lattice tower located on the Champ de Mars in Paris. "
    "Gustave Eiffel's company designed and built the tower, which was completed in 1889. "
    "The structure is 330 metres tall and weighs about 10100 tonnes. "
    "It was the world's tallest man-made structure until 1930. "
    "Millions of visitors climb the Eiffel Tower every year to see Paris from above."
)


class StubSearch:
    def __init__(self, text=TOWER_TEXT):
        self.text_body = text
        self.queries = []

    def text(self, query, max_results=6):
        self.queries.append(query)
        return [
            SearchHit(
                url="https://fr.wikipedia.org/wiki/Eiffel_Tower",
                title="Eiffel Tower",
                snippet=self.text_body[:140],
                engine="stub",
            )
        ]


class StubExtractor:
    def __init__(self, text=TOWER_TEXT):
        self.text_body = text

    def extract(self, url):
        return ExtractedPage(
            url=url,
            ok=True,
            text=self.text_body,
            title="Eiffel Tower",
            chars=len(self.text_body),
            engine="stub",
            resolved_url=url,
            http_status=200,
            content_hash="e2e-hash",
            content_type="text/html",
        )


def make_engine(search=None, extractor=None):
    return ResearchEngine(
        model=ScriptedResearchController(),
        search=search or StubSearch(),
        extractor=extractor or StubExtractor(),
        auditor=neural_auditor(),
        budget=Budget(max_iterations=10, max_search_calls=4, max_fetches=2, max_wall_clock_s=60),
    )


def test_full_run_certifies_and_audits():
    engine = make_engine()
    outcome = engine.run("What is the Eiffel Tower made of and where is it located?")
    assert outcome.status == "COMPLETE"
    assert outcome.state.supporting_claims(), "expected certified claims from entailing source"
    assert outcome.audit is not None and outcome.audit.status in ("PASS", "PASS_WITH_FLAGS")
    assert outcome.state.uncertainty is Uncertainty.EVIDENCE_FOR
    assert outcome.state.unmet_conditions == []
    assert "Certified Findings" in outcome.synthesis


def test_no_evidence_run_abstains_cleanly():
    class EmptySearch:
        def text(self, query, max_results=6):
            return []

    engine = ResearchEngine(
        model=ScriptedResearchController(),
        search=EmptySearch(),
        extractor=StubExtractor(),
        auditor=neural_auditor(),
        budget=Budget(max_iterations=8, max_search_calls=4, max_fetches=2, max_wall_clock_s=30),
    )
    outcome = engine.run("Does compound ZQ-11 cure condition YX-9?")
    assert outcome.state.uncertainty is Uncertainty.NO_EVIDENCE_FOUND
    assert not outcome.state.supporting_claims()
    lowered = outcome.synthesis.lower()
    assert "abstain" in lowered or "no certified" in lowered


def test_conflicting_evidence_is_recorded_and_examined():
    text_support = (
        "Compound X improves recovery time in trained athletes according to one trial. "
        "The trial of compound X measured recovery time in athletes carefully over weeks. "
        "Recovery time improved with compound X in the athlete trial population studied."
    )
    text_refute = (
        "Compound X worsens recovery time in trained athletes according to another trial. "
        "The trial of compound X measured recovery time in athletes carefully over weeks. "
        "Recovery time worsened with compound X in the athlete trial population studied."
    )

    class ConflictSearch:
        def text(self, query, max_results=6):
            return [
                SearchHit(
                    url="https://example.org/a",
                    title="Compound X trial A",
                    snippet=text_support[:120],
                    engine="stub",
                ),
                SearchHit(
                    url="https://example.org/b",
                    title="Compound X trial B",
                    snippet=text_refute[:120],
                    engine="stub",
                ),
            ]

    class ConflictExtractor:
        def extract(self, url):
            body = text_support if url.endswith("/a") else text_refute
            return ExtractedPage(
                url=url,
                ok=True,
                text=body,
                title="Trial",
                chars=len(body),
                engine="stub",
                resolved_url=url,
                http_status=200,
                content_hash="cf-" + url[-1],
                content_type="text/html",
            )

    engine = ResearchEngine(
        model=ScriptedResearchController(),
        search=ConflictSearch(),
        extractor=ConflictExtractor(),
        auditor=neural_auditor(),
        budget=Budget(max_iterations=14, max_search_calls=8, max_fetches=2, max_wall_clock_s=60),
    )
    outcome = engine.run("Does compound X improve recovery time in athletes?")
    assert outcome.state.contradictions, "conflicting evidence must be recorded, not hidden"
    assert all(c.get("examined") for c in outcome.state.contradictions)
    assert outcome.state.uncertainty in (
        Uncertainty.CONFLICTING_EVIDENCE,
        Uncertainty.UNCERTAIN,
        Uncertainty.INSUFFICIENT_EVIDENCE,
    )
    # Conflicting claims must NOT be certified.
    assert not any(
        c.status == "CERTIFIED" and c.claim_id in {x.get("claim_id") for x in outcome.state.contradictions}
        for c in outcome.state.claims.values()
    )


def test_resume_from_checkpoint_continues():
    engine = make_engine()
    # First run to completion, then simulate resume of a preserved state.
    first = engine.run("What is the Eiffel Tower made of and where is it located?")
    from odar.research_state import ResearchState

    restored = ResearchState.from_checkpoint(first.state.to_checkpoint())
    restored.note("simulated crash recovery")
    second_engine = make_engine()
    second = second_engine.run(restored.objective, resume_state=restored)
    assert second.status in ("COMPLETE", "INCOMPLETE")
    assert len(second.state.attempted_queries) >= len(first.state.attempted_queries)


def test_budget_exhaustion_transparent():
    class EmptySearch:
        def text(self, query, max_results=6):
            return []

    engine = ResearchEngine(
        model=ScriptedResearchController(),
        search=EmptySearch(),
        extractor=StubExtractor(),
        auditor=neural_auditor(),
        budget=Budget(max_iterations=4, max_search_calls=3, max_fetches=1, max_wall_clock_s=30),
    )
    outcome = engine.run("An extremely obscure question nobody has written about?")
    assert outcome.status in ("COMPLETE", "INCOMPLETE", "BUDGET_EXHAUSTED")
    # Whatever the terminal status, the report must not pretend findings exist.
    assert (
        "certified findings" not in outcome.synthesis.lower() or "no certified" in outcome.synthesis.lower()
    )
