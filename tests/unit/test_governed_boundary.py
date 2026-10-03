"""REGRESSION (P0 FIX 2 + FIX 5): one governed execution boundary.

Every side-effecting capability must consume governor budget exactly once
per actual side effect, and deliberate attempts to drive the engine with
zero budget prove that policy controls still apply.
"""

from odar.budget import Budget, BudgetExceeded, Governor
from odar.citation_auditor import CitationAuditor, DeterministicNLIScorer
from odar.evidence import Claim
from odar.schemas import ExtractedPage, SearchHit, new_id
from odar.tools import GovernedExecutor


class CountingSearch:
    def __init__(self, results=None):
        self.calls = 0
        self.results = (
            results
            if results is not None
            else [SearchHit(url="https://example.com/x", title="T", snippet="s", engine="stub")]
        )

    def text(self, query, max_results=6):
        self.calls += 1
        return list(self.results)


class CountingExtractor:
    def __init__(self):
        self.calls = 0

    def extract(self, url):
        self.calls += 1
        return ExtractedPage(url=url, ok=False, error="stub")


def make_executor(max_search=10, max_fetch=10, max_sandbox=10, max_model=10, results=None):
    search = CountingSearch(results)
    extractor = CountingExtractor()
    auditor = CitationAuditor()
    auditor.scorer = DeterministicNLIScorer()
    auditor.scorer_backend = "injected"
    governor = Governor(
        Budget(
            max_search_calls=max_search,
            max_fetches=max_fetch,
            max_sandbox_executions=max_sandbox,
            max_model_calls=max_model,
        )
    )
    executor = GovernedExecutor(governor=governor, search=search, extractor=extractor, auditor=auditor)
    return executor, governor, search, extractor


class TestSearchAccounting:
    def test_one_approval_per_actual_search(self):
        executor, governor, search, _ = make_executor()
        for i in range(3):
            executor.search(f"query {i}")
        assert search.calls == 3
        assert governor.counters["search_calls"] == 3  # ACTUAL == ACCOUNTED

    def test_denied_search_performs_no_network_work(self):
        executor, governor, search, _ = make_executor(max_search=1)
        executor.search("allowed")
        try:
            executor.search("denied")
            raised = False
        except BudgetExceeded:
            raised = True
        assert raised
        assert search.calls == 1  # denied call never reached the backend

    def test_failing_search_with_retry_never_overdraws(self):
        class ExplodingSearch:
            def __init__(self):
                self.calls = 0

            def text(self, query, max_results=6):
                self.calls += 1
                raise ConnectionError("network down")

        search = ExplodingSearch()
        governor = Governor(Budget(max_search_calls=5, max_retries_per_call=2))
        executor = GovernedExecutor(governor=governor, search=search, extractor=CountingExtractor())
        hits = executor.search("anything")
        assert hits == []
        # One budget unit approved; retries bounded by the budget's
        # max_retries_per_call (initial call + at most 2 retries).
        assert governor.counters["search_calls"] == 1
        assert search.calls == 3


class TestFetchAccounting:
    def test_one_approval_per_fetch_and_duplicate_suppression(self):
        executor, governor, _, extractor = make_executor()
        fetched: list = []
        executor.fetch("https://example.com/a", "objective", fetched)
        assert extractor.calls == 1
        assert governor.counters["fetches"] == 1
        # Same URL again: suppressed BEFORE any approval or side effect.
        outcome = executor.fetch("https://example.com/a", "objective", fetched)
        assert outcome.denied
        assert extractor.calls == 1
        assert governor.counters["fetches"] == 1


class TestSandboxAccounting:
    def test_sandbox_requires_approval(self):
        executor, governor, _, _ = make_executor(max_sandbox=1)
        digest = executor.run_python("print(1)")
        assert digest["ok"] is True
        try:
            executor.run_python("print(2)")
            raised = False
        except BudgetExceeded:
            raised = True
        assert raised
        assert governor.counters["sandbox_executions"] == 1


class TestEvaluationAccounting:
    def test_evaluation_requires_model_budget(self):
        executor, governor, _, _ = make_executor(max_model=1)
        claim = Claim(claim_id=new_id("clm"), text="A claim about towers in Paris generally.")
        try:
            executor.evaluate_claim(claim, [], [])
        except BudgetExceeded:
            pass
        try:
            executor.evaluate_claim(claim, [], [])
            raised = False
        except BudgetExceeded:
            raised = True
        assert raised
        assert governor.counters["model_calls"] == 1


class TestDialecticBudgetInvariant:
    """ACTUAL SEARCH CALLS == ACCOUNTED SEARCH CALLS for counter-evidence."""

    def test_counter_queries_each_consume_exactly_one_budget_unit(self):
        executor, governor, search, _ = make_executor(max_search=5)
        result = executor.dialectic(
            "the tower was completed in 1889",
            evaluator=None,  # semantic-less run: still must be budget-correct
            max_counter_queries=2,
        )
        assert len(result.counter_queries) == 2
        assert search.calls == 2
        assert governor.counters["search_calls"] == 2

    def test_dialectic_stops_cleanly_when_budget_runs_out(self):
        executor, governor, search, _ = make_executor(max_search=1)
        result = executor.dialectic(
            "the tower was completed in 1889",
            evaluator=None,
            max_counter_queries=3,
        )
        # Only one counter-query could be paid for; no hidden searches.
        assert search.calls == 1 == governor.counters["search_calls"]
        assert len(result.counter_queries) == 1
        assert result.verdict == "UNDECIDED"  # no evaluator -> cues never decide

    def test_maximum_counter_queries_enforced(self):
        executor, governor, search, _ = make_executor(max_search=50)
        result = executor.dialectic("h", evaluator=None, max_counter_queries=0)
        assert result.counter_queries == []
        assert search.calls == 0


class TestEngineRoutesThroughExecutor:
    """Deliberately attempt to drive engine actions with exhausted budgets:
    policy/resource controls must still apply via the single boundary."""

    def _engine(self, budget: Budget, search_calls: list):
        from odar.engine import ResearchEngine
        from odar.llm import ScriptedResearchController

        class WatchSearch:
            def text(self, query, max_results=6):
                search_calls.append(query)
                return [SearchHit(url="https://example.com/x", title="T", snippet="s", engine="stub")]

        class WatchExtractor:
            def extract(self, url):
                search_calls.append("fetch:" + url)
                return ExtractedPage(url=url, ok=False, error="stub")

        auditor = CitationAuditor()
        auditor.scorer = DeterministicNLIScorer()
        auditor.scorer_backend = "injected"
        return ResearchEngine(
            model=ScriptedResearchController(),
            search=WatchSearch(),
            extractor=WatchExtractor(),
            auditor=auditor,
            budget=budget,
        )

    def test_zero_search_budget_means_zero_network_searches(self):
        calls: list = []
        engine = self._engine(
            Budget(max_iterations=3, max_search_calls=0, max_fetches=2, max_wall_clock_s=20), calls
        )
        outcome = engine.run("Anything at all?")
        real_searches = [c for c in calls if not c.startswith("fetch:")]
        assert real_searches == []
        assert outcome.status in ("COMPLETE", "INCOMPLETE", "BUDGET_EXHAUSTED")

    def test_zero_fetch_budget_means_zero_page_fetches(self):
        calls: list = []
        engine = self._engine(
            Budget(max_iterations=4, max_search_calls=2, max_fetches=0, max_wall_clock_s=20), calls
        )
        outcome = engine.run("Anything at all about towers?")
        assert all(not c.startswith("fetch:") for c in calls)
        assert outcome.status in ("COMPLETE", "INCOMPLETE", "BUDGET_EXHAUSTED")
