"""UNIT suite: engine internals - governor, controller adaptivity, budgets,
cancellation, checkpoint round-trip, telemetry redaction, LLM parsing."""

import json
import time

import pytest

from odar.budget import Budget, BudgetExceeded, CancellationToken, Governor
from odar.engine import ResearchEngine
from odar.final_audit import FinalAuditor
from odar.llm import (
    AuthBackendError,
    BackendTimeoutError,
    ModelBackendError,
    RateLimitedBackendError,
    ScriptedResearchController,
    ServerBackendError,
    classify_backend_error,
)
from odar.research_state import ResearchState
from odar.telemetry import TelemetryRecorder


class TestGovernor:
    def test_denials_are_recorded(self):
        governor = Governor(Budget(max_search_calls=1))
        governor.approve_search()
        with pytest.raises(BudgetExceeded):
            governor.approve_search()
        assert governor.denials
        report = governor.report()
        assert len(report["denials"]) >= 1

    def test_extension_requires_allowlisted_justification(self):
        governor = Governor(Budget(max_iterations=4))
        assert governor.request_extension("feels wrong") is False
        before = governor.budget.max_iterations
        assert governor.request_extension("unresolved_contradiction: A vs B") is True
        assert governor.budget.max_iterations > before

    def test_deadline_token_cancels(self):
        token = CancellationToken(deadline=time.monotonic() - 1)
        assert token.is_cancelled is True
        assert token.reason == "wall_clock_deadline"


class TestScriptedControllerAdaptivity:
    def test_opens_with_search(self):
        controller = ScriptedResearchController()
        state = ResearchState(objective="How do honeybees navigate?")
        decision = controller.decide(state, {})
        assert decision.action == "search"

    def test_reacts_to_empty_search_by_reformulating(self):
        controller = ScriptedResearchController()
        state = ResearchState(objective="How do honeybees navigate using polarized light?")
        state.attempted_queries.append("honeybees navigate polarized light")
        state.iteration = 1
        decision = controller.decide(state, {})
        assert decision.action == "search"
        assert decision.params["query"] != "honeybees navigate polarized light"

    def test_abstains_after_exhausted_reformulations(self):
        controller = ScriptedResearchController()
        state = ResearchState(objective="How do honeybees navigate?")
        # Every candidate probe the controller can generate has been tried.
        state.attempted_queries.extend(
            [
                "honeybees navigate",
                "How do honeybees navigate",
                "honeybees navigate",
            ]
        )
        state.iteration = 3
        decision = controller.decide(state, {})
        assert decision.action == "finish"
        assert decision.params["uncertainty"] == "no_evidence_found"

    def test_reacts_to_open_contradiction(self):
        controller = ScriptedResearchController()
        state = ResearchState(objective="Q")
        from odar.evidence import Claim

        state.attempted_queries.append("initial query")
        state.iteration = 1
        claim = Claim(claim_id="clm_1", text="some claim", status="CONTRADICTED")
        state.add_claim(claim)
        state.contradictions.append({"claim_id": "clm_1", "examined": False})
        decision = controller.decide(state, {})
        assert decision.action == "dialectic_attack"
        assert decision.params["claim_id"] == "clm_1"


class TestEngineGovernance:
    def _engine_with_stub(self, search_hits=None, page_text=None):
        from odar.schemas import ExtractedPage

        class StubSearch:
            def text(self, query, max_results=6):
                return search_hits or []

        class StubExtractor:
            def extract(self, url):
                if not page_text:
                    return ExtractedPage(url=url, ok=False, error="no content")
                return ExtractedPage(
                    url=url,
                    ok=True,
                    text=page_text,
                    title="T",
                    chars=len(page_text),
                    engine="stub",
                    resolved_url=url,
                    http_status=200,
                    content_hash="cafe",
                    content_type="text/html",
                )

        engine = ResearchEngine(
            model=ScriptedResearchController(),
            search=StubSearch(),
            extractor=StubExtractor(),
            budget=Budget(max_iterations=6, max_search_calls=4, max_fetches=2, max_wall_clock_s=60),
        )
        return engine

    def test_budget_exhaustion_is_reported_not_hidden(self):
        engine = self._engine_with_stub(search_hits=[])
        outcome = engine.run("An unanswerable question about thing Q?")
        assert outcome.status in ("COMPLETE", "BUDGET_EXHAUSTED", "INCOMPLETE")
        assert outcome.state.uncertainty.value in ("NO_EVIDENCE_FOUND", "INSUFFICIENT_EVIDENCE", "UNCERTAIN")
        # failure transparency: the report must not invent findings
        assert (
            "certified findings" not in outcome.synthesis.lower()
            or "no certified" in outcome.synthesis.lower()
        )

    def test_cancellation_propagates_to_loop(self):
        from odar.schemas import SearchHit

        hits = [SearchHit(url="https://example.com/a", title="A", snippet="slow", engine="x")]
        engine = self._engine_with_stub(search_hits=hits, page_text="x" * 300)
        # Simulates an external cancel arriving just as the run starts; the
        # loop must observe it at its first checkpoint and stop cleanly.
        engine.token.cancel("test_cancel")
        outcome = engine.run("Question about anything at all here?")
        assert outcome.status == "CANCELLED"


class TestCheckpointRoundTrip:
    def test_state_survives_serialization(self):
        from odar.evidence import Claim, EvidenceItem, Relation, SourceRecord, Uncertainty
        from odar.schemas import new_id

        state = ResearchState(objective="Objective X")
        source = SourceRecord(
            source_id=new_id("src"),
            url="https://example.com/x",
            title="T",
            extracted_text="text body",
            http_status=200,
        )
        state.add_source(source)
        claim = Claim(
            claim_id=new_id("clm"), text="Claim Y", status="CERTIFIED", uncertainty=Uncertainty.EVIDENCE_FOR
        )
        state.add_claim(claim)
        item = EvidenceItem(
            evidence_id=new_id("ev"),
            claim_id=claim.claim_id,
            source_id=source.source_id,
            span="text body",
            relation=Relation.SUPPORTS,
            entailment_probability=0.9,
            contradiction_probability=0.05,
            relevance_score=0.8,
            evaluated_by="test",
        )
        state.add_evidence(item)
        state.record_query("query one")

        restored = ResearchState.from_checkpoint(state.to_checkpoint())
        assert restored.objective == "Objective X"
        assert len(restored.sources) == 1
        assert len(restored.claims) == 1
        assert list(restored.claims.values())[0].status == "CERTIFIED"
        assert len(restored.evidence) == 1
        assert list(restored.evidence.values())[0].relation is Relation.SUPPORTS
        assert restored.attempted_queries == ["query one"]


class TestTelemetryRedaction:
    def test_secrets_never_recorded(self):
        recorder = TelemetryRecorder()
        recorder.event("probe", "run1", payload="key sk-ant-api03-abcdefghijklmnopqr")
        dumped = json.dumps(recorder.dump_events())
        assert "sk-ant-api03-abcdefghijklmnopqr" not in dumped


class TestBackendErrorClassification:
    def test_auth_failures_classified(self):
        class FakeAuth(Exception):
            status_code = 401

        err = classify_backend_error(FakeAuth("bad key"))
        assert isinstance(err, AuthBackendError)

    def test_rate_limit_classified(self):
        class FakeRateLimit(Exception):
            status_code = 429

        assert isinstance(classify_backend_error(FakeRateLimit("slow down")), RateLimitedBackendError)

    def test_server_error_classified(self):
        class Fake500(Exception):
            status_code = 503

        assert isinstance(classify_backend_error(Fake500("upstream")), ServerBackendError)

    def test_timeout_classified(self):
        assert isinstance(classify_backend_error(TimeoutError("timed out")), BackendTimeoutError)

    def test_generic_classified(self):
        assert isinstance(classify_backend_error(ValueError("odd")), ModelBackendError)


class TestNativeToolDispatch:
    """Tool dispatch must route through the GovernedExecutor only."""

    def _controller_and_executor(self):
        from odar.llm import NativeToolUseController
        from odar.tools import GovernedExecutor
        from odar.citation_auditor import CitationAuditor, DeterministicNLIScorer
        from odar.schemas import ExtractedPage, SearchHit

        class StubSearch:
            def __init__(self):
                self.calls = 0

            def text(self, query, max_results=6):
                self.calls += 1
                return [SearchHit(url="https://example.com/x", title="T", snippet="s", engine="stub")]

        class StubExtractor:
            def extract(self, url):
                return ExtractedPage(url=url, ok=False, error="stubbed off")

        class FakeAdapter:
            pass

        search = StubSearch()
        auditor = CitationAuditor()
        auditor.scorer = DeterministicNLIScorer()
        auditor.scorer_backend = "injected"
        executor = GovernedExecutor(
            governor=Governor(Budget(max_search_calls=2, max_fetches=1)),
            search=search,
            extractor=StubExtractor(),
            auditor=auditor,
        )
        controller = NativeToolUseController(adapter=FakeAdapter())
        return controller, executor, search

    def test_web_search_dispatch_uses_executor(self):
        controller, executor, search = self._controller_and_executor()
        state = ResearchState(objective="Q")
        output, is_error = controller._dispatch("web_search", {"query": "bees"}, state, executor)
        assert not is_error and "example.com" in output
        assert search.calls == 1

    def test_budget_denied_tool_returns_error_result(self):
        controller, executor, search = self._controller_and_executor()
        state = ResearchState(objective="Q")
        for _ in range(2):
            controller._dispatch("web_search", {"query": "bees"}, state, executor)
        output, is_error = controller._dispatch("web_search", {"query": "bees"}, state, executor)
        assert is_error and "denied by governor" in output
        assert search.calls == 2  # denied call performed NO network work

    def test_unknown_tool_rejected(self):
        controller, executor, _ = self._controller_and_executor()
        state = ResearchState(objective="Q")
        output, is_error = controller._dispatch("shell_exec", {"cmd": "ls"}, state, executor)
        assert is_error and "unknown tool" in output


class TestFinalAuditorFlags:
    def test_hallucinated_citation_detected(self):
        state = ResearchState(objective="Q")
        synthesis = "Finding per [src_doesnotexist] shows the result."
        report = FinalAuditor().audit(state, synthesis)
        assert report.checks["citations_resolve"] is False
        assert report.status == "FAIL"

    def test_clean_report_passes(self):
        from odar.evidence import Claim, EvidenceItem, Relation, SourceRecord, Uncertainty
        from odar.schemas import new_id

        state = ResearchState(objective="Q")
        source = SourceRecord(
            source_id=new_id("src"),
            url="https://example.com/x",
            http_status=200,
            retrieved_at=time.time(),
            title="T",
            content_hash="abc123",
            extracted_text="The tower is 330 metres tall.",
        )
        state.add_source(source)
        claim = Claim(
            claim_id=new_id("clm"),
            text="The tower is 330 metres tall.",
            status="CERTIFIED",
            uncertainty=Uncertainty.EVIDENCE_FOR,
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
            evaluated_by="test",
        )
        state.add_evidence(item)
        synthesis = (
            f"Finding per [{source.source_id}]: the tower is 330 metres tall. "
            "Limitations: uncertainty remains about further details."
        )
        report = FinalAuditor().audit(state, synthesis)
        assert report.checks["citations_resolve"] is True
        assert report.checks["no_circular_citations"] is True
        assert report.status in ("PASS", "PASS_WITH_FLAGS")
