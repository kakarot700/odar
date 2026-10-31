"""REGRESSION: 2026-10-31 live run on "What is the evidence that regular
aerobic exercise reduces resting blood pressure?" (scripted controller, NLI
verifier) certified reporting boilerplate and vague tangents at p>=0.99 while
the on-question blood-pressure findings never reached certification:

1. Methods/aims/eligibility boilerplate ("The search term applied was: ...")
   is always entailed by its own paper, so it passed NLI and consumed the
   claim budget.  It is reporting text, not a finding: excluded at mining.
2. The 2-token overlap floor mined vague sentences ("Regular physical
   exercise has several beneficial effects on overall health.") for an
   8-term question.  Overlap is now stemmed and needs a third hit once the
   question has more than 4 terms.

The sentences below are verbatim from that run's report (PMC6306777 /
PMC6557987).
"""

from __future__ import annotations

import time

from odar.engine import (
    ResearchEngine,
    _candidate_sentences,
    _looks_like_methods_boilerplate,
    _stem_token,
)
from odar.evidence import Claim, SourceRecord
from odar.llm import ScriptedResearchController
from odar.research_state import ResearchState
from odar.schemas import new_id

BP_QUESTION = "What is the evidence that regular aerobic exercise reduces resting blood pressure?"

SEARCH_TERMS_SENTENCE = (
    "The search term applied was: “(resting heart rate or resting pulse rate or resting heart "
    "frequency or resting pulse frequency) and (sport or training or exercise or yoga or tai chi "
    "or qigong) and control*”."
)
AIM_SENTENCE = (
    "The aim of the systematic review was to assess whether regular exercise or sports have an "
    "impact on the RHR in healthy subjects by taking different types of sports into account."
)
ELIGIBILITY_SENTENCE = (
    "The intervention should have comprised any type of sports and/or exercise including aerobic "
    "and anaerobic (e.g., high intensity interval training), endurance (including ball and team "
    "sports) or strength training, school sports, yoga, qigong, or tai chi."
)
VAGUE_SENTENCE = "Regular physical exercise has several beneficial effects on overall health."
LIFESPAN_SENTENCE = (
    "Regular exercise and physical activity throughout a lifespan can improve life expectancy "
    "and disability-adjusted life expectancy, as shown in many studies."
)
FINDING_SENTENCE = (
    "Regular aerobic exercise training significantly reduces resting systolic and diastolic "
    "blood pressure in healthy adults compared with no exercise."
)
# A real (if tangential) finding from the fetched RHR paper: still minable.
MORTALITY_SENTENCE = (
    "This effect may contribute to a reduction in all-cause mortality due to regular exercise or sports."
)


def source(text: str) -> SourceRecord:
    return SourceRecord(
        source_id=new_id("src"),
        url="https://pmc.ncbi.nlm.nih.gov/articles/PMC6306777/",
        resolved_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC6306777/",
        http_status=200,
        retrieved_at=time.time(),
        title="Effects of Exercise on the Resting Heart Rate",
        content_hash="abc123def4567890",
        origin="external",
        extracted_text=text,
    )


class TestMethodsBoilerplateFilter:
    def test_observed_boilerplate_sentences_flagged(self):
        for sentence in (SEARCH_TERMS_SENTENCE, AIM_SENTENCE, ELIGIBILITY_SENTENCE):
            assert _looks_like_methods_boilerplate(sentence), sentence

    def test_boilerplate_never_mined_as_candidates(self):
        page = "\n".join([SEARCH_TERMS_SENTENCE, AIM_SENTENCE, ELIGIBILITY_SENTENCE, FINDING_SENTENCE])
        assert _candidate_sentences(page) == [FINDING_SENTENCE]

    def test_findings_not_flagged(self):
        for sentence in (FINDING_SENTENCE, VAGUE_SENTENCE, MORTALITY_SENTENCE):
            assert not _looks_like_methods_boilerplate(sentence), sentence


class TestStemmedRelevanceGate:
    def test_stemmer_unifies_inflections(self):
        assert _stem_token("reduces") == _stem_token("reduction") == _stem_token("reduced")
        assert _stem_token("studies") == _stem_token("study")
        assert _stem_token("resting") == _stem_token("rest")
        assert _stem_token("exercise") == "exercise"  # no false suffix match

    def test_vague_two_hit_sentence_not_mined_for_long_question(self):
        state = ResearchState(objective=BP_QUESTION)
        state.add_source(source("\n".join([VAGUE_SENTENCE, LIFESPAN_SENTENCE, FINDING_SENTENCE])))
        ResearchEngine(model=ScriptedResearchController())._action_extract_claims(state)
        mined = [c.text for c in state.claims.values()]
        assert FINDING_SENTENCE in mined
        assert VAGUE_SENTENCE not in mined
        assert LIFESPAN_SENTENCE not in mined

    def test_genuine_tangential_finding_still_mined(self):
        state = ResearchState(objective=BP_QUESTION)
        state.add_source(source(MORTALITY_SENTENCE))
        ResearchEngine(model=ScriptedResearchController())._action_extract_claims(state)
        assert [c.text for c in state.claims.values()] == [MORTALITY_SENTENCE]

    def test_short_questions_keep_two_hit_floor(self):
        state = ResearchState(objective="Does coffee raise blood pressure?")
        text = "Coffee consumption raises systolic blood pressure acutely in most adults studied."
        state.add_source(source(text))
        ResearchEngine(model=ScriptedResearchController())._action_extract_claims(state)
        assert [c.text for c in state.claims.values()] == [text]


def test_pyproject_declares_ddgs_for_web_app():
    """pip install must satisfy odar.web.ask's `from ddgs import DDGS`."""
    from pathlib import Path

    pyproject = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()
    assert '"ddgs>=' in pyproject


# --------------------------------------------------------------------------- #
# Free-tier refutation gate: symmetric NLI noise without an LLM judge
# --------------------------------------------------------------------------- #
BP_CLAIM = (
    "Regular physical exercise decreases resting heart rate, blood pressure, and atherogenic "
    "markers, and increases physiological cardiac hypertrophy (13–15, 28)."
)
# Same-topic, different-fact span the small cross-encoder scored p_con=0.99 BOTH
# ways against BP_CLAIM on the 2026-10-31 live run - symmetric noise, so the
# reverse check alone could not save the claim, and with no LLM judge in the
# scripted tier it flipped the most on-question claim to CONFLICTING.
NOISE_SPAN = (
    "Exercise improves myocardial perfusion and increases high-density lipoprotein (HDL) "
    "cholesterol levels, all of which reduce stress on the heart and improve cardiovascular "
    "function in healthy and diseased individuals (11, 15, 29, 30)."
)
GENUINE_REFUTATION = (
    "However, aerobic exercise did not significantly reduce resting systolic blood pressure "
    "in the pooled analysis of the included trials."
)


class TestFreeTierRefutationGate:
    """No judge configured: only a symmetric contradiction that engages the
    claim's own terms confirms; every other credible signal abstains."""

    def _auditor(self, table):
        auditor = _neural(table)
        auditor.refutation_judge = None
        return auditor

    def test_symmetric_noise_without_bigram_contact_abstains(self):
        # Observed 2026-10-31: p_con=0.99 both ways, zero shared bigrams.
        auditor = self._auditor({(NOISE_SPAN, BP_CLAIM): (0.99, 0.0), (BP_CLAIM, NOISE_SPAN): (0.99, 0.0)})
        assert auditor.confirm_refutations([NOISE_SPAN], BP_CLAIM, [[0.99, 0.0, 0.01]]) == []
        assert auditor._last_unadjudicated == [0]

    def test_paraphrased_dispute_without_shared_bigram_also_abstains(self):
        """A genuine dispute can be phrased without restating the claim's
        bigrams; offline it is indistinguishable from noise, so it must get
        the same abstention - never discarded into a certification."""
        paraphrase = "Trials found exercise raised pressure; hypertrophy was not observed and rate unchanged."
        auditor = self._auditor({(paraphrase, BP_CLAIM): (0.99, 0.0), (BP_CLAIM, paraphrase): (1.0, 0.0)})
        assert auditor.confirm_refutations([paraphrase], BP_CLAIM, [[0.99, 0.0, 0.01]]) == []
        assert auditor._last_unadjudicated == [0]

    def test_asymmetric_signal_abstains_not_dropped(self):
        """Negation/subgroup/numeric disputes usually score one-directionally
        (real model: 0.99 fwd / <0.13 rev).  They are not confirmed
        refutations, but the claim must not certify over them either."""
        dispute = "However, aerobic exercise did not significantly reduce resting systolic blood pressure."
        auditor = self._auditor({(dispute, BP_CLAIM): (0.99, 0.0), (BP_CLAIM, dispute): (0.1, 0.0)})
        assert auditor.confirm_refutations([dispute], BP_CLAIM, [[0.99, 0.0, 0.01]]) == []
        assert auditor._last_unadjudicated == [0]

    def test_symmetric_refutation_engaging_claim_terms_confirms(self):
        auditor = self._auditor(
            {(GENUINE_REFUTATION, BP_CLAIM): (0.99, 0.0), (BP_CLAIM, GENUINE_REFUTATION): (0.99, 0.0)}
        )
        assert auditor.confirm_refutations([GENUINE_REFUTATION], BP_CLAIM, [[0.99, 0.0, 0.01]]) == [0]
        assert auditor._last_unadjudicated == []

    def test_judge_path_unchanged(self):
        """With a judge configured, topical gate + symmetric check admit
        candidates and the judge adjudicates (COP30 contract in
        test_benchmark_fixes): no bigram gate, asymmetric stays dropped."""
        auditor = _neural({(NOISE_SPAN, BP_CLAIM): (0.99, 0.0), (BP_CLAIM, NOISE_SPAN): (0.99, 0.0)})
        auditor.refutation_judge = lambda claim, span: True
        assert auditor.confirm_refutations([NOISE_SPAN], BP_CLAIM, [[0.99, 0.0, 0.01]]) == [0]
        assert auditor._last_unadjudicated == []


# --------------------------------------------------------------------------- #
# Provenance + engine abstention on unadjudicated contradiction signals
# --------------------------------------------------------------------------- #
class _TableScorer:
    """Neural-looking stub: (premise, hypothesis) -> (p_con, p_ent) table."""

    def __init__(self, table):
        self.table = table

    def predict(self, pairs):
        import math

        rows = []
        for premise, hypothesis in pairs:
            p_con, p_ent = self.table.get((premise, hypothesis), (0.0, 0.0))
            p_neu = max(0.0, 1.0 - p_con - p_ent)
            rows.append([math.log(p + 1e-9) for p in (p_con, p_ent, p_neu)])
        return rows


def _neural(table):
    from odar.citation_auditor import CitationAuditor

    auditor = CitationAuditor()
    auditor.scorer = _TableScorer(table)
    auditor.scorer_backend = "table-neural-stub"
    return auditor


def test_unadjudicated_span_becomes_disputed_evidence_not_refutation():
    """Observed noise pair (2026-10-31): symmetric p_con=0.99, no shared bigram."""
    table = {
        (NOISE_SPAN, BP_CLAIM): (0.99, 0.0),
        (BP_CLAIM, NOISE_SPAN): (0.99, 0.0),
        (BP_CLAIM, BP_CLAIM): (0.0, 0.99),  # its own verbatim span supports
    }
    auditor = _neural(table)
    auditor.refutation_judge = None
    text = BP_CLAIM + "\n" + NOISE_SPAN
    claim = Claim(claim_id=new_id("clm"), text=BP_CLAIM)
    _result, items, _ = auditor.audit_with_provenance(claim, [source(text)], [text])
    relations = {i.relation for i in items}
    from odar.evidence import Relation

    assert Relation.REFUTES not in relations
    assert Relation.DISPUTED in relations
    assert Relation.SUPPORTS in relations  # its own verbatim span still supports


def test_engine_abstains_instead_of_certifying_over_disputed(monkeypatch):
    """supports + disputed (no confirmed refutation): UNCERTAIN, never CERTIFIED."""
    from odar.engine import ResearchEngine
    from odar.evidence import Claim, EvidenceItem, Relation, Uncertainty
    from odar.llm import ScriptedResearchController
    from odar.schemas import ClaimAuditResult, Verdict

    state = ResearchState(objective=BP_QUESTION)
    src = source(BP_CLAIM + "\n" + NOISE_SPAN)
    state.add_source(src)
    claim = Claim(claim_id=new_id("clm"), text=BP_CLAIM, uncertainty=Uncertainty.UNCERTAIN)
    state.add_claim(claim)
    result = ClaimAuditResult(
        claim=claim.text,
        verdict=Verdict.ENTAILMENT,
        entailment_probability=0.99,
        contradiction_probability=0.0,
        threshold=0.75,
        spans_checked=2,
        certified=True,
        scorer_backend="table-neural-stub",
        provisional=False,
    )
    items = [
        EvidenceItem(
            evidence_id=new_id("ev"),
            claim_id=claim.claim_id,
            source_id=src.source_id,
            span=BP_CLAIM,
            relation=Relation.SUPPORTS,
            entailment_probability=0.99,
            contradiction_probability=0.0,
            relevance_score=1.0,
            circularity_score=0.0,
            evaluated_by="table-neural-stub",
        ),
        EvidenceItem(
            evidence_id=new_id("ev"),
            claim_id=claim.claim_id,
            source_id=src.source_id,
            span=NOISE_SPAN,
            relation=Relation.DISPUTED,
            entailment_probability=0.0,
            contradiction_probability=0.99,
            relevance_score=0.5,
            circularity_score=0.0,
            evaluated_by="table-neural-stub",
        ),
    ]
    engine = ResearchEngine(model=ScriptedResearchController())
    monkeypatch.setattr(engine.executor, "evaluate_claim", lambda *a, **k: (result, items, []))
    engine._action_evaluate({}, state)
    assert claim.status == "UNCERTAIN"
    assert not state.supporting_claims()
    assert state.contradictions and state.contradictions[0]["stance"] == "unconfirmed-offline"


# Verbatim from the same run's report (the claims that stayed UNCERTAIN).
BP_CLAIM_RHR = (
    "Regular physical exercise decreases resting heart rate, blood pressure, and atherogenic "
    "markers, and increases physiological cardiac hypertrophy (13–15, 28)."
)
BP_CLAIM_METABOLIC = (
    "Studies have shown that exercise can improve metabolic and cardiovascular health independent "
    "of changes in body weight, including improved glucose homeostasis (80, 81), endothelial "
    "function (82), blood pressure (83), and HDL levels (84, 85)."
)


def _answer_section(report: str) -> str:
    return report.split("## Answer", 1)[1].split("\n## ", 1)[0]


class TestAnswerRelevanceGate:
    """Observed 2026-10-31, same BP run: the certified mortality tangent (3
    stemmed hits - exactly the mining floor, none of them blood-pressure
    terms, with a dangling "This effect") was presented as THE answer while
    the on-question BP claims stayed UNCERTAIN.  The Answer is now anchored
    only by certified claims one hit above the mining floor; otherwise the
    report says the on-question evidence is insufficient and lists the
    certified tangent and the uncertain findings separately."""

    def _report(self, claims, objective=BP_QUESTION) -> str:
        from odar.engine import ResearchOutcome, ResearchEngine
        from odar.llm import ScriptedResearchController

        state = ResearchState(objective=objective)
        state.add_source(source(FINDING_SENTENCE))
        for text, status in claims:
            claim = Claim(claim_id=new_id("clm"), text=text, status=status)
            state.add_claim(claim)
        engine = ResearchEngine(model=ScriptedResearchController())
        return engine._build_synthesis(state, ResearchOutcome(state=state))

    def test_bar_is_one_above_mining_floor(self):
        from odar.engine import _answer_relevance_bar, _stemmed_term_hits
        from odar.llm import compact_query

        assert _answer_relevance_bar(8) == 4 and _answer_relevance_bar(4) == 3
        terms = {_stem_token(t) for t in compact_query(BP_QUESTION, max_terms=24).split()}
        assert _stemmed_term_hits(MORTALITY_SENTENCE, terms) == 3  # tangent: below the bar
        assert _stemmed_term_hits(FINDING_SENTENCE, terms) >= 4  # finding: above the bar

    def test_certified_tangent_is_not_presented_as_the_answer(self):
        report = self._report(
            [
                (MORTALITY_SENTENCE, "CERTIFIED"),
                (BP_CLAIM_RHR, "UNCERTAIN"),
                (BP_CLAIM_METABOLIC, "UNCERTAIN"),
            ]
        )
        answer = _answer_section(report)
        assert "Insufficient on-question evidence" in answer
        assert MORTALITY_SENTENCE not in answer
        # the tangent stays visible, marked tangential - not deleted
        assert f"{MORTALITY_SENTENCE} *(certified, but tangential to the asked outcome)*" in report
        # the useful on-question findings are preserved, separately
        uncertain = report.split("## Uncertain Findings (not certified)", 1)[1]
        assert BP_CLAIM_RHR in uncertain and BP_CLAIM_METABOLIC in uncertain

    def test_on_question_certified_claim_still_composes_the_answer(self):
        report = self._report([(FINDING_SENTENCE, "CERTIFIED")])
        assert "Insufficient on-question evidence" not in report
        assert FINDING_SENTENCE in _answer_section(report)
        assert "tangential" not in report

    def test_uncertain_section_only_when_the_answer_abstains(self):
        report = self._report([(FINDING_SENTENCE, "CERTIFIED"), (BP_CLAIM_RHR, "UNCERTAIN")])
        assert "## Uncertain Findings (not certified)" not in report
