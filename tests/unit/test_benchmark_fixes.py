"""REGRESSION: fixes from the 2026-10-07 ODAR vs GPT Researcher benchmark.

1. Verifier: clear COP30 facts were scored SUPPORTS (their own verbatim span)
   and REFUTES at once, so every claim became CONFLICTING and the run
   abstained.  Root causes: (a) navigation items differing only in a list
   enumerator ("... summit 4." vs "... summit 6.") reached the cross-encoder
   as distinct premises/claims; (b) the small NLI cross-encoder labels
   topically adjacent but unrelated sentences as "contradiction".
2. Synthesis: reports listed raw certified sentences with no readable answer
   and no clickable URLs.
3. Fetch: the model guessed URLs (CBO, IGM, Wikipedia) that 403/404'd.
4. Retry: one Token Harbor HTTP 400 killed a whole run.

The probability tables below are the REAL cross-encoder/nli-deberta-v3-small
outputs observed on the Carbon Brief COP30 page (bench/diag, 2026-10-07).
"""

from __future__ import annotations

import asyncio
import math

import pytest

from odar.budget import Budget, BudgetExceeded, Governor
from odar.citation_auditor import (
    CitationAuditor,
    _sentence_split,
    is_about_claim,
    normalize_span,
)
from odar.engine import ResearchEngine, _candidate_sentences
from odar.evidence import Claim, Relation, SourceRecord
from odar.llm import (
    AuthBackendError,
    BadRequestBackendError,
    NativeToolUseController,
    RateLimitedBackendError,
    ServerBackendError,
    call_backend_with_retry,
    classify_backend_error,
    fetch_refusal,
    normalize_fetch_url,
    parse_judgement,
    validate_synthesis,
)
from odar.research_state import ResearchState
from odar.schemas import ExtractedPage, SearchHit, new_id

# --------------------------------------------------------------------------- #
# COP30 fixtures (verbatim from the benchmark sources)
# --------------------------------------------------------------------------- #
COP30_FACT = (
    "A voluntary plan to curb fossil fuels, a goal to triple adaptation finance and new efforts "
    "to strengthen climate targets have been launched at the COP30 climate summit in Brazil."
)
COP30_CONCLUDED = (
    "After 13 days of negotiations, the COP30 climate summit concluded on Saturday in Belem, "
    "marking a series of advances and discussions that will continue over the coming months."
)
COP30_ANTALYA = (
    "After more than three years of dispute, it was agreed at COP30 that next year's summit will "
    "take place in Antalya, Turkey, with rival bidder Australia acting as president of negotiations."
)
COP30_ACCOMMODATION = (
    "In August, just three months before COP30, the Brazilian government launched the summit's "
    "accommodation booking platform, following pressure to do so."
)
COP30_DING = (
    "During the leaders summit in Belem on the eve of COP30, Ding's speech contained few surprises "
    "and did not mention China's provision of south-south climate finance."
)
NAV_4 = "Interactive: Tracking negotiating texts at the COP30 climate summit\n4."
NAV_6 = "Interactive: Tracking negotiating texts at the COP30 climate summit\n6."
NAV_CLAIM = "Interactive: Tracking negotiating texts at the COP30 climate summit 4."


def _logits(p_con: float, p_ent: float) -> list:
    p_neu = max(1e-6, 1.0 - p_con - p_ent)
    return [math.log(max(p_con, 1e-6)), math.log(max(p_ent, 1e-6)), math.log(p_neu)]


class TableNeuralScorer:
    """Stands in for an AVAILABLE neural cross-encoder (not the lexical
    fallback), replaying observed (premise, hypothesis) probabilities."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def predict(self, pairs):
        rows = []
        for premise, hypothesis in pairs:
            self.calls.append((premise, hypothesis))
            if premise.strip() == hypothesis.strip():
                rows.append(_logits(0.0, 0.98))
                continue
            p_con, p_ent = self.table.get((premise, hypothesis), (0.01, 0.01))
            rows.append(_logits(p_con, p_ent))
        return rows


def neural(table) -> CitationAuditor:
    auditor = CitationAuditor()
    auditor.scorer = TableNeuralScorer(table)
    auditor.scorer_backend = "table-neural-stub"
    return auditor


def source(text: str, url: str = "https://www.carbonbrief.org/cop30-key-outcomes") -> SourceRecord:
    import time

    return SourceRecord(
        source_id=new_id("src"),
        url=url,
        resolved_url=url,
        http_status=200,
        retrieved_at=time.time(),
        title="COP30: Key outcomes agreed at the UN climate talks in Belem",
        content_hash="abc123def4567890",
        origin="external",
        extracted_text=text,
    )


# --------------------------------------------------------------------------- #
# 1. Verifier
# --------------------------------------------------------------------------- #
class TestSpanNormalisation:
    def test_enumerators_stripped(self):
        assert (
            normalize_span("Interactive: Tracking texts at COP30\n4.")
            == "Interactive: Tracking texts at COP30"
        )
        assert normalize_span("  3. The summit ended.  ") == "The summit ended."

    def test_nav_items_differing_only_by_number_collapse(self):
        spans = _sentence_split(NAV_4 + "\n" + NAV_6)
        assert len(spans) == 1

    def test_line_breaks_are_span_boundaries(self):
        spans = _sentence_split("COP30: Key outcomes\nThe summit agreed a new adaptation goal.")
        assert spans == ["COP30: Key outcomes", "The summit agreed a new adaptation goal."]


class TestClaimCandidates:
    def test_headlines_labels_questions_rejected(self):
        text = "\n".join(
            [
                "COP30: Key Outcomes Agreed At The UN Climate Talks In Belem.",
                NAV_4,
                "COP30: What does the Baku to Belem roadmap mean for climate finance?",
                COP30_FACT,
            ]
        )
        assert _candidate_sentences(text) == [COP30_FACT]

    def test_short_fragments_rejected(self):
        assert _candidate_sentences("The COP30 summit concluded in Belem.") == []


class TestRefutationGate:
    def test_cop30_fact_not_both_supported_and_refuted(self):
        """Observed: accommodation sentence p_con=0.78 forward, neutral reverse."""
        table = {(COP30_ACCOMMODATION, COP30_FACT): (0.78, 0.0)}
        auditor = neural(table)
        text = " ".join([COP30_FACT, COP30_ACCOMMODATION, COP30_CONCLUDED, COP30_ANTALYA])
        claim = Claim(claim_id=new_id("clm"), text=COP30_FACT)
        result, items, _ = auditor.audit_with_provenance(claim, [source(text)], [text])
        relations = {i.relation for i in items}
        assert Relation.SUPPORTS in relations
        assert Relation.REFUTES not in relations
        assert result.certified is True

    def test_nav_duplicate_cannot_refute(self):
        """Observed: '...summit 4.' vs '...summit 6.' p_con=1.00 both ways."""
        auditor = neural({})
        text = NAV_4 + "\n" + NAV_6 + "\n" + COP30_FACT
        claim = Claim(claim_id=new_id("clm"), text=normalize_span(NAV_CLAIM))
        _result, items, _ = auditor.audit_with_provenance(claim, [source(text)], [text])
        assert not any(i.relation is Relation.REFUTES for i in items)

    def test_one_directional_contradiction_is_noise(self):
        auditor = neural({(COP30_ANTALYA, COP30_CONCLUDED): (0.99, 0.0)})
        assert auditor.confirm_refutations([COP30_ANTALYA], COP30_CONCLUDED, [[0.99, 0.0, 0.01]]) == []

    def test_symmetric_contradiction_still_refutes(self):
        negated = "The COP30 climate summit did not conclude in Belem after 13 days of negotiations."
        table = {(negated, COP30_CONCLUDED): (0.95, 0.01), (COP30_CONCLUDED, negated): (0.96, 0.01)}
        auditor = neural(table)
        assert auditor.confirm_refutations([negated], COP30_CONCLUDED, [[0.95, 0.01, 0.04]]) == [0]

    def test_off_topic_span_fails_topical_gate(self):
        assert not is_about_claim("The weather in Lisbon was mild.", COP30_FACT)
        assert is_about_claim(COP30_ACCOMMODATION, "COP30 accommodation booking platform launched in August.")

    def test_judge_overrules_same_topic_different_event(self):
        """Observed: Ding speech vs 'concluded in Belem' p_con=1.00 / reverse 0.99."""
        table = {(COP30_DING, COP30_CONCLUDED): (1.0, 0.0), (COP30_CONCLUDED, COP30_DING): (0.99, 0.0)}
        auditor = neural(table)
        verdicts = []

        def judge(claim, span):
            verdicts.append(span)
            return False  # "UNRELATED"

        auditor.refutation_judge = judge
        text = " ".join([COP30_CONCLUDED, COP30_DING, COP30_FACT, COP30_ACCOMMODATION])  # 4 spans
        claim = Claim(claim_id=new_id("clm"), text=COP30_CONCLUDED)
        result, items, _ = auditor.audit_with_provenance(claim, [source(text)], [text])
        assert verdicts, "judge must be consulted for symmetric contradictions"
        assert not any(i.relation is Relation.REFUTES for i in items)
        assert result.certified is True

    @pytest.mark.parametrize("verdict", [True, None])
    def test_judge_true_or_unavailable_keeps_refutation(self, verdict):
        table = {(COP30_DING, COP30_CONCLUDED): (1.0, 0.0), (COP30_CONCLUDED, COP30_DING): (0.99, 0.0)}
        auditor = neural(table)
        auditor.refutation_judge = lambda claim, span: verdict
        assert auditor.confirm_refutations([COP30_DING], COP30_CONCLUDED, [[1.0, 0.0, 0.0]]) == [0]

    def test_judge_result_cached(self):
        table = {(COP30_DING, COP30_CONCLUDED): (1.0, 0.0), (COP30_CONCLUDED, COP30_DING): (0.99, 0.0)}
        auditor = neural(table)
        calls = []
        auditor.refutation_judge = lambda c, s: calls.append(1) or False
        for _ in range(3):
            auditor.confirm_refutations([COP30_DING], COP30_CONCLUDED, [[1.0, 0.0, 0.0]])
        assert len(calls) == 1

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("CONTRADICTS", True),
            ("unrelated.", False),
            ("Consistent", False),
            ("", None),
            ("CONTRADICTS or CONSISTENT", None),
        ],
    )
    def test_parse_judgement(self, text, expected):
        assert parse_judgement(text) is expected


# --------------------------------------------------------------------------- #
# 2. Synthesis
# --------------------------------------------------------------------------- #
class TestValidateSynthesis:
    def test_drops_uncited_and_unresolvable_sentences(self):
        text = "COP30 launched a fossil fuel plan [1]. Everyone celebrated. It also set targets [9]. Finance tripled [2]."
        assert validate_synthesis(text, 2) == "COP30 launched a fossil fuel plan [1]. Finance tripled [2]."

    def test_empty_when_nothing_cited(self):
        assert validate_synthesis("No citations here. None at all.", 3) == ""


# --------------------------------------------------------------------------- #
# 3. Fetch allow-list
# --------------------------------------------------------------------------- #
class TestFetchAllowList:
    def test_normalisation(self):
        assert normalize_fetch_url("https://WWW.Example.org/a/#frag") == "example.org/a"
        assert normalize_fetch_url("ftp://example.org/a") == ""

    def test_guessed_url_refused(self):
        state = ResearchState(objective="Q")
        assert "not returned by any web_search" in fetch_refusal("https://www.cbo.gov/guessed", state)

    def test_search_result_allowed_and_failing_domain_skipped(self):
        state = ResearchState(objective="Q")
        state.search_hit_urls.append(normalize_fetch_url("https://www.cbo.gov/report/"))
        assert fetch_refusal("https://cbo.gov/report", state) == ""
        state.failed_approaches.extend(["fetch_domain:cbo.gov", "fetch_domain:cbo.gov"])
        assert "failed 2 times" in fetch_refusal("https://cbo.gov/report", state)


# --------------------------------------------------------------------------- #
# 4. Retry
# --------------------------------------------------------------------------- #
class _StatusError(Exception):
    def __init__(self, status):
        super().__init__(f"status {status}")
        self.status_code = status


def _run_retry(errors, governor=None):
    attempts = []
    sleeps = []

    async def factory():
        attempts.append(1)
        if errors:
            raise errors.pop(0)
        return "ok"

    async def sleep(delay):
        sleeps.append(delay)

    result = asyncio.run(call_backend_with_retry(factory, governor=governor, sleep=sleep))
    return result, attempts, sleeps


class TestBackendRetry:
    def test_classification(self):
        assert isinstance(classify_backend_error(_StatusError(400)), BadRequestBackendError)
        assert isinstance(classify_backend_error(_StatusError(429)), RateLimitedBackendError)
        assert isinstance(classify_backend_error(_StatusError(503)), ServerBackendError)

    def test_429_and_5xx_retried_with_growing_backoff(self):
        governor = Governor(Budget())
        result, attempts, sleeps = _run_retry([_StatusError(429), _StatusError(502)], governor)
        assert result == "ok" and len(attempts) == 3
        assert sleeps[1] > sleeps[0] >= 2.0
        assert governor.counters["retries"] == 2

    def test_400_retried_once_then_raised(self):
        with pytest.raises(BadRequestBackendError):
            _run_retry([_StatusError(400), _StatusError(400)])
        result, attempts, _ = _run_retry([_StatusError(400)])
        assert result == "ok" and len(attempts) == 2

    def test_auth_never_retried(self):
        with pytest.raises(AuthBackendError):
            _run_retry([_StatusError(401)])

    def test_persistent_5xx_gives_up(self):
        with pytest.raises(ServerBackendError):
            _run_retry([_StatusError(500)] * 10)

    def test_retry_budget_is_governed(self):
        governor = Governor(Budget(max_retries_per_call=0))
        with pytest.raises(BudgetExceeded):
            _run_retry([_StatusError(503)], governor)


# --------------------------------------------------------------------------- #
# End to end on the production (tool-use) path with a fake adapter
# --------------------------------------------------------------------------- #
CB_URL = "https://www.carbonbrief.org/cop30-key-outcomes/"
GUESSED_URL = "https://en.wikipedia.org/wiki/2025_UN_Climate_Change_Conference"
CB_TEXT = "\n".join(
    [
        "COP30: Key Outcomes Agreed At The UN Climate Talks In Belem",
        NAV_4,
        NAV_6,
        COP30_FACT,
        COP30_CONCLUDED,
        COP30_ANTALYA,
        COP30_ACCOMMODATION,
    ]
)


class FakeAdapter:
    """Scripted Anthropic adapter: search, a guessed fetch, a real fetch, finish."""

    def __init__(self, synthesis="COP30 launched a voluntary fossil fuel plan and an adaptation goal [1]."):
        self.turn = 0
        self.synthesis = synthesis
        self.text_prompts = []

    async def next_response(self, messages, tools, trace):
        from odar.agent import ModelEvent

        script = [
            [{"type": "tool_use", "id": "t1", "name": "web_search", "input": {"query": "COP30 outcomes"}}],
            [{"type": "tool_use", "id": "t2", "name": "fetch_page", "input": {"url": GUESSED_URL}}],
            [{"type": "tool_use", "id": "t3", "name": "fetch_page", "input": {"url": CB_URL}}],
            [{"type": "tool_use", "id": "t4", "name": "finish_research", "input": {}}],
        ]
        content = script[min(self.turn, len(script) - 1)]
        self.turn += 1
        return ModelEvent(role="assistant", content=content, stop_reason="tool_use")

    async def complete_text(self, prompt, system_prompt, max_tokens=1024):
        self.text_prompts.append(prompt)
        if "Answer with exactly one word" in prompt:
            return "UNRELATED"
        if isinstance(self.synthesis, Exception):
            raise self.synthesis
        return self.synthesis


class StubSearch:
    def text(self, query, max_results=6):
        return [SearchHit(url=CB_URL, title="COP30: Key outcomes", snippet="COP30 outcomes", engine="stub")]


class StubExtractor:
    def __init__(self):
        self.calls = []

    def extract(self, url):
        self.calls.append(url)
        return ExtractedPage(
            url=url,
            ok=True,
            text=CB_TEXT,
            title="COP30: Key outcomes agreed at the UN climate talks in Belem",
            chars=len(CB_TEXT),
            engine="stub",
            resolved_url=url,
            http_status=200,
            content_hash="cb30cb30",
            content_type="text/html",
        )


def _engine(adapter, extractor):
    table = {
        (COP30_ACCOMMODATION, COP30_FACT): (0.78, 0.0),
        (COP30_ANTALYA, COP30_CONCLUDED): (1.0, 0.0),
        (COP30_CONCLUDED, COP30_ANTALYA): (0.99, 0.0),
        (COP30_ANTALYA, COP30_FACT): (0.97, 0.0),
        (COP30_FACT, COP30_ANTALYA): (0.95, 0.0),
    }
    return ResearchEngine(
        model=NativeToolUseController(adapter, max_tool_turns=6),
        search=StubSearch(),
        extractor=extractor,
        auditor=neural(table),
        budget=Budget(
            max_iterations=4, max_search_calls=4, max_fetches=4, max_model_calls=30, max_wall_clock_s=60
        ),
    )


QUESTION = "What were the main outcomes and agreements of the COP30 UN climate summit held in Belem, Brazil?"


class TestProductionPathEndToEnd:
    def test_cop30_certifies_with_readable_answer_and_links(self):
        extractor = StubExtractor()
        adapter = FakeAdapter()
        outcome = _engine(adapter, extractor).run(QUESTION)
        state = outcome.state
        # 3. guessed URL refused before any network I/O
        assert GUESSED_URL not in extractor.calls and CB_URL in extractor.calls
        # 1. no clear fact is left CONFLICTING by NLI noise
        assert not [c for c in state.claims.values() if c.status == "CONFLICTING"]
        assert state.supporting_claims(), "clear COP30 facts must certify"
        # 2. readable answer with clickable citations + sources list
        report = outcome.synthesis
        assert "## Answer" in report
        assert f"[[1]]({CB_URL})" in report
        assert "## Sources" in report and f"]({CB_URL})" in report
        assert outcome.audit is not None and outcome.audit.status != "FAIL"
        assert outcome.status == "COMPLETE"

    def test_synthesis_failure_falls_back_to_deterministic_answer(self):
        adapter = FakeAdapter(synthesis=ServerBackendError("down"))
        outcome = _engine(adapter, StubExtractor()).run(QUESTION)
        assert "## Answer" in outcome.synthesis
        assert f"]({CB_URL})" in outcome.synthesis
        assert outcome.status == "COMPLETE"


@pytest.mark.integration
class TestRealCrossEncoderCop30:
    """Runs the REAL cross-encoder (downloads weights): `pytest -m integration`."""

    def test_no_false_conflicts_on_cop30_page(self):
        auditor = CitationAuditor()
        auditor.ensure_scorer()
        if auditor.scorer_backend != auditor.model_name:
            pytest.skip("neural cross-encoder unavailable")
        text = CB_TEXT
        # Before the fix both of these were SUPPORTS + REFUTES (CONFLICTING).
        for sentence in (COP30_FACT, normalize_span(NAV_CLAIM)):
            claim = Claim(claim_id=new_id("clm"), text=sentence)
            _r, items, _ = auditor.audit_with_provenance(claim, [source(text)], [text])
            relations = {i.relation for i in items}
            assert not (Relation.SUPPORTS in relations and Relation.REFUTES in relations), sentence


class TestReportPolish:
    def test_sources_list_titles_not_numeric_claims(self):
        from odar.final_audit import FinalAuditor

        state = ResearchState(objective="Q")
        report = "## Sources\n1. [src_abc123] [PostgreSQL: Documentation: 14: VACUUM](https://www.postgresql.org/docs/14/x)"
        audit = FinalAuditor().audit(state, report)
        assert audit.checks["numeric_consistency"] is True

    def test_repeated_identical_citations_collapsed(self):
        adapter = FakeAdapter(synthesis="COP30 launched a voluntary fossil fuel plan [1][1].")
        outcome = _engine(adapter, StubExtractor()).run(QUESTION)
        assert f"[[1]]({CB_URL})[[1]]({CB_URL})" not in outcome.synthesis


class TestBenchmarkRound2:
    """Second-round findings from the 2026-10-07 rerun (q3)."""

    def test_abbreviation_does_not_split_claim(self):
        from odar.citation_auditor import split_sentences

        line = (
            "Update, Feb. 9: A CBO report published Feb. 8 estimates a $15 minimum wage would reduce "
            "employment in 2025 by 1.4 million workers. The average estimate is higher."
        )
        parts = split_sentences(line)
        assert len(parts) == 2 and parts[0].startswith("Update, Feb. 9")

    def test_synthesis_drops_unsupported_attribution(self):
        facts = [
            "A CBO report published Feb. 8 estimates a $15 minimum wage would reduce employment by 1.4 million."
        ]
        text = (
            "Moody's Analytics estimates 1.4 million job losses [1]. "
            "The CBO estimates employment would fall by 1.4 million [1]. "
            "It would cost 2.7 million jobs [1]."
        )
        assert (
            validate_synthesis(text, 1, facts=facts)
            == "The CBO estimates employment would fall by 1.4 million [1]."
        )
