"""feat/best-research: fewer model calls, longer single-call reports, effort
scaling, batched contradiction judging, fetch fallbacks and trust surfaces."""

from __future__ import annotations

import json
import math
import re

from odar.deep import (
    informativeness,
    parse_plan,
    parse_plan_meta,
    parse_report,
    report_prompt,
)
from odar.evidence import SourceRecord
from odar.llm import BatchRefutationJudge, parse_batch_judgement
from odar.research_state import ResearchState
from odar.retrieval import (
    PageExtractor,
    ZeroCostSearch,
    _should_fall_back,
    parse_reader_markdown,
    parse_wayback_available,
)
from odar.schemas import ExtractedPage

from tests.unit.test_deep_research import PAGES, QUESTION, PipelineClient, _deep


# --------------------------------------------------------------------------- #
# Planner: effort scaling
# --------------------------------------------------------------------------- #
def test_plan_meta_reads_kind_and_academic_flag():
    text = json.dumps({"kind": "fact", "academic": False, "subquestions": []})
    assert parse_plan_meta(text) == ("fact", False)
    assert parse_plan_meta('{"kind": "research", "academic": true, "subquestions": [') == ("research", True)
    assert parse_plan_meta("garbage") == ("research", False)


def test_fact_plan_keeps_three_queries():
    text = json.dumps(
        {
            "kind": "fact",
            "subquestions": [
                {
                    "question": "Which city hosted COP29?",
                    "queries": ["COP29 host city", "COP29 Baku", "COP29 wiki"],
                }
            ],
        }
    )
    plan = parse_plan(text, "Which city hosted COP29?", 1, max_queries=3)
    assert len(plan) == 1 and len(plan[0].queries) == 3


# --------------------------------------------------------------------------- #
# Single-call writer
# --------------------------------------------------------------------------- #
def test_report_prompt_and_parser_round_trip():
    facts = ["Alpha is one.", "Beta is two.", "Gamma is three."]
    prompt = report_prompt("Q?", [(1, "First", [0, 1], 100), (2, "Second", [2], 80)], facts, 150)
    assert "[3] Gamma is three." in prompt and "=== SECTION 2 ===" in prompt
    reply = "=== SUMMARY ===\nAlpha is one [1].\n\n=== SECTION 1 ===\nBeta is two [2].\n=== SECTION 2 ===\nGamma [3]."
    parts = parse_report(reply)
    assert parts == {"summary": "Alpha is one [1].", "1": "Beta is two [2].", "2": "Gamma [3]."}
    assert parse_report("no markers here") == {}


class SingleWriterClient(PipelineClient):
    """Answers the single-call report prompt in its marker format."""

    def complete(self, model, prompt, system, max_tokens):
        if "=== SUMMARY ===" not in prompt:
            return super().complete(model, prompt, system, max_tokens)
        with self._lock:
            self.calls.append((model, prompt))
        out = ["=== SUMMARY ==="]
        facts = re.findall(r"^\[(\d+)\] (.+)$", prompt, flags=re.M)
        out.append(" ".join(f"{t.rstrip('.')} [{n}]." for n, t in facts[:2]))
        current = None
        for line in prompt.splitlines():
            m = re.match(r"^SECTION (\d+): ", line)
            if m:
                current = m.group(1)
                out.append(f"=== SECTION {current} ===")
            m = re.match(r"^\[(\d+)\] (.+)$", line)
            if m and current:
                out.append(f"{m.group(2).rstrip('.')} [{m.group(1)}].")
        return "\n".join(out)


def test_single_call_writer_uses_one_writer_call():
    client = SingleWriterClient()
    engine, _s, _e = _deep(client)
    outcome = engine.run(QUESTION)
    assert outcome.status == "COMPLETE", outcome.state.notes
    roles = outcome.governance["pipeline"]["router"]["calls_by_role"]
    assert roles["writer"] == 1
    assert outcome.governance["pipeline"]["writer_mode"] == "single"
    report = outcome.synthesis
    assert "### Summary" in report and "### Confidence and evidence" in report
    assert "### How does PostgreSQL MVCC provide snapshot isolation?" in report
    assert re.search(r"\[\[\d+\]\]\(https://", report.split("## Certified Findings")[0])


def test_unusable_single_reply_falls_back_to_per_section_writers():
    engine, _s, _e = _deep(PipelineClient())  # never emits the markers
    outcome = engine.run(QUESTION)
    assert outcome.status == "COMPLETE"
    assert outcome.telemetry["counters"].get("single_writer_fallback") == 1
    assert "### Summary" in outcome.synthesis


def test_sections_mode_is_still_available():
    engine, _s, _e = _deep(SingleWriterClient())
    engine.writer_mode = "sections"
    outcome = engine.run(QUESTION)
    assert outcome.governance["pipeline"]["router"]["calls_by_role"]["writer"] >= 2


# --------------------------------------------------------------------------- #
# Batched contradiction judge
# --------------------------------------------------------------------------- #
def test_batch_judgement_parser():
    text = "1: CONTRADICTS\nPAIR 2 - unrelated\n3) **CONSISTENT**\n9: CONTRADICTS"
    assert parse_batch_judgement(text, 3) == {1: True, 2: False, 3: False}


def test_batch_judge_collects_then_resolves_in_one_call():
    class Controller:
        def __init__(self):
            self.prompts = []

        def complete_text(self, prompt, system, governor, max_tokens=64):
            self.prompts.append(prompt)
            return "1: UNRELATED\n2: CONTRADICTS"

    controller = Controller()
    judge = BatchRefutationJudge(controller, governor=None)
    assert judge("claim a", "span a") is None  # not collecting: never calls the model
    judge.collecting = True
    judge("claim a", "span a")
    judge("claim b", "span b")
    judge("claim a", "span a")  # duplicate pair recorded once
    verdicts = judge.resolve()
    assert len(controller.prompts) == 1
    assert sorted(verdicts.values(), key=str) == [False, True]
    assert judge.pending == []


CONFLICT_PAGES = dict(PAGES)
CONFLICT_PAGES["https://forum.example.com/vacuum-myth"] = (
    "Vacuum myth",
    "PostgreSQL VACUUM never reclaims the storage occupied by dead tuples that are no longer visible to "
    "any transaction.",
)


class ConflictScorer:
    """'never' vs its positive twin is a symmetric contradiction; otherwise a
    sentence entails itself."""

    def predict(self, pairs):
        rows = []
        for premise, hypothesis in pairs:
            p, h = premise.lower(), hypothesis.lower()
            hyp = set(h.split())
            overlap = len(hyp & set(p.split())) >= 0.8 * len(hyp)
            if (
                ("never reclaims" in p) != ("never reclaims" in h)
                and "dead tuples" in p
                and "dead tuples" in h
            ):
                probs = [0.9, 0.05, 0.05]
            elif overlap:
                probs = [0.01, 0.97, 0.02]
            else:
                probs = [0.01, 0.01, 0.98]
            rows.append([math.log(x) for x in probs])
        return rows


class JudgeClient(SingleWriterClient):
    def __init__(self, verdict):
        super().__init__()
        self.verdict = verdict
        self.judge_calls = 0

    def complete(self, model, prompt, system, max_tokens):
        if "For each numbered pair" in prompt:
            with self._lock:
                self.calls.append((model, prompt))
                self.judge_calls += 1
            count = len(re.findall(r"^PAIR \d+", prompt, flags=re.M))
            return "\n".join(f"{i}: {self.verdict}" for i in range(1, count + 1))
        return super().complete(model, prompt, system, max_tokens)


def _conflict_engine(client):
    import tests.unit.test_deep_research as base

    original = base.PAGES
    base.PAGES = CONFLICT_PAGES
    try:
        engine, search, extractor = _deep(client)
    finally:
        base.PAGES = original
    engine.auditor.scorer = ConflictScorer()

    class Search:
        def text(self, query, max_results=6):
            from odar.schemas import SearchHit

            return [SearchHit(url=u, title=t, snippet=t + " PostgreSQL MVCC VACUUM", engine="fake")
                    for u, (t, _) in CONFLICT_PAGES.items()]  # fmt: skip

    class Extractor:
        def extract(self, url):
            title, text = CONFLICT_PAGES[url]
            text = (text + " ") * 2
            return ExtractedPage(url=url, ok=True, text=text, title=title, chars=len(text), engine="fake",
                                 resolved_url=url, http_status=200, content_hash=str(abs(hash(url)))[:12],
                                 content_type="text/html")  # fmt: skip

    engine.executor.search_backend = Search()
    engine.executor.extractor = Extractor()
    return engine


def test_dismissed_conflicts_are_reverified_without_extra_judge_calls():
    client = JudgeClient("UNRELATED")
    engine = _conflict_engine(client)
    outcome = engine.run(QUESTION)
    assert outcome.status == "COMPLETE", outcome.state.notes
    roles = outcome.governance["pipeline"]["router"]["calls_by_role"]
    assert roles.get("judge", 0) == client.judge_calls == 1  # several pairs, ONE call
    counters = outcome.telemetry["counters"]
    assert counters["judge_pairs_batched"] >= 2
    assert counters["judge_dismissed_conflicts"] >= 1
    assert not outcome.state.contradictions  # dismissed conflicts leave no record
    assert all(c.status == "CERTIFIED" for c in outcome.state.claims.values())


def test_confirmed_conflicts_are_surfaced_in_the_report():
    client = JudgeClient("CONTRADICTS")
    engine = _conflict_engine(client)
    outcome = engine.run(QUESTION)
    assert outcome.state.contradictions and client.judge_calls == 1
    assert "### Where sources disagree" in outcome.synthesis
    assert "Contradicted by" in outcome.synthesis
    assert "never reclaims" in outcome.synthesis.split("## Certified Findings")[0]


# --------------------------------------------------------------------------- #
# Claim mining quality
# --------------------------------------------------------------------------- #
def test_informativeness_prefers_figures_and_penalises_meta_text():
    assert informativeness("The CBO estimated 1.3 million jobs would be lost by 2025.") > 0
    assert informativeness("Now that we know what xmin looks like, we use it to understand concurrency.") < 0
    assert informativeness("It is also important for many workloads in production systems.") < 0


# --------------------------------------------------------------------------- #
# Retrieval fallbacks (offline)
# --------------------------------------------------------------------------- #
def test_reader_markdown_is_flattened_and_walls_are_rejected():
    body = (
        "Title: Intro\n\nURL Source: https://x\n\nMarkdown Content:\n# Heading\n"
        "Some **bold** text with a [link](https://y.example/a) and ![img](https://i).\n* item one\n---\n"
    )
    title, text = parse_reader_markdown(body)
    assert title == "Intro"
    assert text == "Heading\nSome bold text with a link and .\nitem one"
    walled = "Title: x\n\nWarning: This page maybe requiring CAPTCHA\n\nMarkdown Content:\n"
    assert parse_reader_markdown(walled)[1] == ""


def test_wayback_availability_parsing():
    body = json.dumps(
        {
            "url": "https://www.cbo.gov/publication/55410",
            "archived_snapshots": {
                "closest": {
                    "status": "200",
                    "available": True,
                    "url": "http://web.archive.org/web/20260607021827/https://www.cbo.gov/publication/55410",
                    "timestamp": "20260607021827",
                }
            },
        }
    )
    url, stamp = parse_wayback_available(body)
    assert url == "https://web.archive.org/web/20260607021827id_/https://www.cbo.gov/publication/55410"
    assert stamp == "20260607021827"
    assert parse_wayback_available('{"archived_snapshots": {}}') == ("", "")


def test_fallback_triggers():
    assert _should_fall_back(ExtractedPage(url="u", ok=False, http_status=403, error="HTTP 403"))
    assert _should_fall_back(ExtractedPage(url="u", ok=False, error="ReadTimeout: timed out"))
    assert _should_fall_back(ExtractedPage(url="u", ok=False, http_status=200, error="bot challenge page"))
    assert not _should_fall_back(ExtractedPage(url="u", ok=False, http_status=404, error="HTTP 404"))
    assert not _should_fall_back(ExtractedPage(url="u", ok=False, error="unsafe fetch refused: private"))


def test_extract_falls_back_reader_then_wayback_and_never_for_quarantine(monkeypatch):
    extractor = PageExtractor()
    blocked = ExtractedPage(url="https://site.example/a", ok=False, http_status=403, error="HTTP 403")
    archived = ExtractedPage(url="https://site.example/a", ok=True, text="t" * 300, engine="wayback:20260101")
    order = []
    monkeypatch.setattr(extractor, "_extract_direct", lambda url: blocked)
    monkeypatch.setattr(extractor, "_via_reader", lambda url: order.append("reader"))
    monkeypatch.setattr(extractor, "_via_wayback", lambda url: order.append("wayback") or archived)
    page = extractor.extract("https://site.example/a")
    assert order == ["reader", "wayback"] and page.engine == "wayback:20260101"

    monkeypatch.setenv("ODAR_READER_FALLBACK", "0")
    monkeypatch.setenv("ODAR_WAYBACK_FALLBACK", "0")
    order.clear()
    assert extractor.extract("https://site.example/a").ok is False and order == []

    monkeypatch.delenv("ODAR_READER_FALLBACK")
    monkeypatch.delenv("ODAR_WAYBACK_FALLBACK")
    quarantined = ExtractedPage(url="u", ok=False, quarantined=True, error="unsafe URL rejected")
    monkeypatch.setattr(extractor, "_extract_direct", lambda url: quarantined)
    order.clear()
    assert extractor.extract("u").quarantined and order == []


def test_rss_parser_and_optional_tiers_are_off_by_default(monkeypatch):
    xml = (
        "<rss><channel><item><title>COP30 | UNFCCC</title><link>https://unfccc.int/cop30</link>"
        "<description>Closing &amp; outcomes</description></item><item><link>javascript:x</link></item>"
        "</channel></rss>"
    )
    hits = ZeroCostSearch.parse_rss_items(xml, 5, engine="bing-rss")
    assert [(h.url, h.title, h.snippet) for h in hits] == [
        ("https://unfccc.int/cop30", "COP30 | UNFCCC", "Closing & outcomes")
    ]
    monkeypatch.delenv("ODAR_SEARCH_BING_RSS", raising=False)
    monkeypatch.delenv("ODAR_SEARCH_MARGINALIA", raising=False)
    search = ZeroCostSearch()
    called = []
    monkeypatch.setattr(search, "_search_bing_rss", lambda q, n: called.append("bing") or [])
    assert search._search_optional("q", 5) == [] and called == []


def test_source_route_survives_checkpoint():
    state = ResearchState(objective="q")
    record = SourceRecord(source_id="src_1", url="https://a.example/x", retrieved_via="wayback:20260101")
    state.add_source(record)
    restored = ResearchState.from_checkpoint(state.to_checkpoint())
    assert restored.sources["src_1"].retrieved_via == "wayback:20260101"


def test_broaden_query_splits_identifiers_and_drops_filler():
    from odar.retrieval import broaden_query

    assert broaden_query("What is the default value of autovacuum_vacuum_scale_factor setting") == (
        "default value autovacuum vacuum"
    )


def test_empty_search_is_retried_once_with_a_broader_query(monkeypatch):
    search = ZeroCostSearch()
    seen = []

    def tier(q, n):
        seen.append(q)
        return []

    for name in ("_search_ddgs", "_search_html_fallback", "_search_optional", "_search_wikipedia"):
        monkeypatch.setattr(search, name, tier)
    assert search.text("very specific autovacuum_vacuum_scale_factor default value", 5) == []
    assert len(set(seen)) == 2  # original + one broadened form, never a loop


# --------------------------------------------------------------------------- #
# Speed/depth pass: run cache, shared NLI model, sentence splitting, fact mining,
# grounded rewrite (tests rewritten 2026-10-10 after the original additions to
# this file were lost to a storage read error)
# --------------------------------------------------------------------------- #
def test_split_sentences_keeps_abbreviations_and_decimals_together():
    from odar.citation_auditor import split_sentences

    text = "In Feb. 8 testimony Dr. Card said 30 U.S. states raised wages. The default is 0.2 here."
    assert split_sentences(text) == [
        "In Feb. 8 testimony Dr. Card said 30 U.S. states raised wages.",
        "The default is 0.2 here.",
    ]
    assert split_sentences("Wages rose. 8 estimates differ.") == ["Wages rose. 8 estimates differ."]


def test_validate_synthesis_keeps_an_abbreviated_sentence_whole():
    from odar.llm import validate_synthesis

    facts = ["30 U.S. states have passed minimum wage laws above the federal level."]
    out = validate_synthesis("Some 30 U.S. states have passed laws [1].", 1, facts=facts)
    assert out.strip() == "Some 30 U.S. states have passed laws [1]."


def test_clean_mined_sentence_and_answer_type_bonus():
    from odar.deep import answer_type_bonus, clean_mined_sentence

    assert clean_mined_sentence("4 The minimum wage is $7.25.") == "The minimum wage is $7.25."
    assert clean_mined_sentence("\u2191 Text here.") == "Text here."
    assert clean_mined_sentence("2009 was the year.") == "2009 was the year."
    q = "What is the default value of the scale factor?"
    assert answer_type_bonus(q, "The default is 0.2 (20% of table size).") >= 3
    assert answer_type_bonus(q, "The scale factor controls vacuum timing.") == 0
    assert (
        answer_type_bonus("In what year was it adopted, and at which COP?", "Adopted at COP21 in 2015.") >= 6
    )


def test_repair_prompt_and_parser():
    from odar.deep import parse_repairs, repair_prompt

    facts = ["Alpha is one.", "Beta is two."]
    prompt = repair_prompt("Q?", [("Alpha is huge [1].", [0])], facts)
    assert "ITEM 1: Alpha is huge [1]." in prompt and "[1] Alpha is one." in prompt
    reply = json.dumps({"rewrites": [{"item": 1, "text": "Alpha is one [1]."}, {"item": 2, "text": "DROP"}]})
    assert parse_repairs(reply, 2) == {0: "Alpha is one [1]."}
    assert parse_repairs('junk "item": 2, "text": "Beta is two [2]." junk', 2) == {1: "Beta is two [2]."}
    assert parse_repairs(reply, 0) == {}


def test_apply_repairs_substitutes_or_drops_placeholders():
    from odar.deep import _REPAIR_MARK, DeepResearchEngine

    text = f"Kept [1]. {_REPAIR_MARK}0{_REPAIR_MARK} Also kept [2].\n\n{_REPAIR_MARK}1{_REPAIR_MARK}"
    out = DeepResearchEngine._apply_repairs(text, {0: "Fixed [1]."})
    assert out == "Kept [1]. Fixed [1]. Also kept [2]."


def test_run_cache_serves_repeat_searches_and_fetches_without_budget():
    from tests.unit.test_governed_boundary import make_executor

    executor, governor, search, extractor = make_executor()
    executor.cache_enabled = True
    executor.search("Same  Query")
    executor.search("same query")
    assert search.calls == 1 and governor.counters["search_calls"] == 1

    def good_page(url):
        extractor.calls += 1
        return ExtractedPage(url=url, ok=True, text="x " * 300, chars=600, engine="stub")

    extractor.extract = good_page
    executor.fetch("https://example.com/a", "objective", [])
    executor.fetch("https://example.com/a", "objective", [])  # new run list, cached page
    assert extractor.calls == 1 and governor.counters["fetches"] == 1


def test_run_cache_is_off_by_default():
    from tests.unit.test_governed_boundary import make_executor

    executor, governor, search, _x = make_executor()
    executor.search("q")
    executor.search("q")
    assert search.calls == 2 and governor.counters["search_calls"] == 2


def test_shared_scorer_loads_the_model_once(monkeypatch):
    from odar import citation_auditor as ca

    loads = []

    class FakeModel:
        def predict(self, pairs, *a, **k):
            return [[0.0, 1.0, 0.0] for _ in pairs]

    monkeypatch.setattr(ca, "_SCORER_CACHE", {})
    monkeypatch.setattr(ca, "_load_cross_encoder", lambda name, device: loads.append(name) or FakeModel())
    first = ca.shared_scorer("fake-model", "cpu")
    second = ca.shared_scorer("fake-model", "cpu")
    assert first is second and loads == ["fake-model"]
    assert first.predict([("a", "b")]) == [[0.0, 1.0, 0.0]]


# --------------------------------------------------------------------------- #
# Decimal trust bug: "0.2" must never reach a fact as "2"
# --------------------------------------------------------------------------- #
PG_AUTOVACUUM_HTML = """<html><head><title>PostgreSQL: Automatic Vacuuming</title></head><body>
<div id="docContent"><div class="sect1"><h2>19.10. Automatic Vacuuming</h2><dl>
<dt id="GUC-AUTOVACUUM-VACUUM-SCALE-FACTOR"><code class="varname">autovacuum_vacuum_scale_factor</code>
(<code class="type">floating point</code>)</dt>
<dd><p>Specifies a fraction of the table size to add to <code class="varname">autovacuum_vacuum_threshold</code>
when deciding whether to trigger a <code class="command">VACUUM</code>. The default is
<code class="literal">0.2</code> (20% of table size). This parameter can only be set in the
<code class="filename">postgresql.conf</code> file or on the server command line.</p></dd>
<dt><code class="varname">autovacuum_vacuum_cost_delay</code> (<code class="type">floating point</code>)</dt>
<dd><p>Specifies the cost delay value that will be used in automatic VACUUM operations. The default
value is <code class="literal">0.05</code> seconds in this illustrative sample, written 0<b>.</b>05 here.</p></dd>
<dd><p>Specifies the number of autovacuum worker processes that may be running at any one time.
The default is <code class="literal">3</code>. This parameter can only be set at server start.</p></dd>
</dl></div></div></body></html>"""


def test_html_extraction_keeps_decimals_trafilatura_and_naive():
    extractor = PageExtractor()
    for text in (
        extractor._html_to_text(PG_AUTOVACUUM_HTML, "u"),
        PageExtractor._naive_extract(PG_AUTOVACUUM_HTML),
    ):
        flat = " ".join(text.split())
        assert "The default is 0.2 (20% of table size)." in flat
        assert "0.05 seconds" in flat and "written 0.05 here" in flat
        assert not re.search(r"(?<![\d.])(2 \(20%|005|05 seconds)", flat)
    naive = " ".join(PageExtractor._naive_extract(PG_AUTOVACUUM_HTML).split())
    assert "The default is 3." in naive  # no "3 ." from the inline <code> tag


def test_fetch_path_keeps_decimals(monkeypatch):
    extractor = PageExtractor()
    monkeypatch.setattr(extractor, "_safe_fetch", lambda url: (200, url, "text/html", PG_AUTOVACUUM_HTML))
    page = extractor.extract("https://www.postgresql.org/docs/current/runtime-config-autovacuum.html")
    assert page.ok and "The default is 0.2 (20% of table size)." in " ".join(page.text.split())


def test_mangled_decimals_on_the_source_page_are_detected():
    from odar.citation_auditor import has_mangled_number

    # verbatim from runebook.dev's rewrite of the PostgreSQL docs (2026-10-10)
    assert has_mangled_number("The default value is usually 2 (or 20%).")
    assert has_mangled_number("Set the scale factor to 005 (0.5%) for a busy table named orders.")
    assert has_mangled_number("Lower it to 5 (0.5%) for busy tables.")
    for fine in (
        "The default is 0.2 (20% of table size).",
        "The default is 0.03 (3%).",
        "Of 200 patients, 50 (25%) improved.",
        "Ages 20 (20%) and up.",
        "The office ZIP is 02139 (Cambridge).",
        "Wages rose $7.25 (3%) in 2009.",
    ):
        assert not has_mangled_number(fine), fine


def test_mangled_sentences_never_become_facts_or_evidence():
    from odar.citation_auditor import CitationAuditor
    from odar.engine import _candidate_sentences

    page = (
        "The default value for this setting is usually 2 (or 20%) of the live rows in the table.\n"
        "For a very large table with the default scale factor of 0.2 (20%), autovacuum waits for many dead rows."
    )
    mined = _candidate_sentences(page)
    assert len(mined) == 1 and "0.2 (20%)" in mined[0]
    spans = CitationAuditor().prepare_spans(page, "The default is 0.2.")
    assert spans and not any("usually 2 (or 20%)" in s for s in spans)


# --------------------------------------------------------------------------- #
# Fact questions: the summary states the direct answer
# --------------------------------------------------------------------------- #
F2 = "What is the default value of PostgreSQL's autovacuum_vacuum_scale_factor setting?"
F2_FACTS = [
    "For very large tables (e.g., billions of rows), even a 20% scale factor means a massive number of dead "
    "tuples must accumulate before a vacuum runs.",
    "For a very large table (say, 100 million rows) with the default scale factor of 0.2 (20%), autovacuum "
    "won't run until 20 million rows are dead.",
    "Autovacuum is PostgreSQL's background process for reclaiming dead tuples.",
]


def test_fact_summary_leads_with_the_certified_answer():
    from odar.deep import lead_with_answer

    summary = (
        "This scale factor determines how many dead tuples must accumulate before autovacuum triggers [1]. "
        "Autovacuum reclaims dead tuples [3].\n\nSecond paragraph [3]."
    )
    out = lead_with_answer(F2, summary, F2_FACTS)
    assert out.startswith(F2_FACTS[1] + " [2] This scale factor")
    assert out.endswith("Second paragraph [3].")
    assert "0.2 (20%)" in out.split("[2]")[0]


def test_fact_summary_is_untouched_when_it_already_answers_or_nothing_answers():
    from odar.deep import lead_with_answer

    answered = "The default scale factor is 0.2 (20%) [2]. More [1]."
    assert lead_with_answer(F2, answered, F2_FACTS) == answered
    # no certified fact states the DEFAULT: nothing is promoted
    assert lead_with_answer(F2, "Prose [1].", [F2_FACTS[0], F2_FACTS[2]]) == "Prose [1]."
    assert lead_with_answer(F2, "", F2_FACTS) == F2_FACTS[1] + " [2]"


def test_fact_question_report_states_the_answer_first():
    """Through the report assembly: the writer's summary opens with a
    non-answer; the report's summary opens with the certified answer."""
    from odar.deep import DeepResearchEngine, SubQuestion
    from odar.evidence import Claim

    engine, _s, _e = _deep(SingleWriterClient())
    engine.kind = "fact"
    engine.subquestions = [SubQuestion(index=1, question=F2, queries=[])]
    # (the final report gate re-checks this prose, so it must be entailed by fact 1)
    written = (
        "A scale factor means a massive number of dead tuples must accumulate before a vacuum runs [1].",
        [""],
    )
    engine._write_single = lambda *_a, **_k: written  # type: ignore[method-assign]
    engine._write_sections = lambda *_a, **_k: written  # type: ignore[method-assign]
    state = ResearchState(objective=F2)
    claims = []
    for number, text in enumerate(F2_FACTS):
        claim = Claim(claim_id=f"clm_{number}", text=text, status="CERTIFIED")
        state.claims[claim.claim_id] = claim
        engine.subquestions[0].claim_ids.append(claim.claim_id)
        engine._claim_subq[claim.claim_id] = 1
        claims.append(claim)
    answer = DeepResearchEngine._answer_text(engine, state, claims, {}, {})
    summary = answer.split("### Summary", 1)[1].split("###", 1)[0].strip()
    assert summary.startswith(F2_FACTS[1])
    assert "A scale factor means a massive number of dead tuples" in summary
