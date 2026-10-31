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
from odar.evidence import SourceRecord
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
    """Without a refutation judge, a refutation must engage the claim's terms."""

    def _auditor(self):
        from odar.citation_auditor import CitationAuditor

        auditor = CitationAuditor()
        auditor.scorer_backend = "table-neural-stub"
        return auditor

    def test_noise_span_shares_no_content_bigram(self):
        from odar.citation_auditor import shares_content_bigram

        assert not shares_content_bigram(NOISE_SPAN, BP_CLAIM)
        assert shares_content_bigram(GENUINE_REFUTATION, BP_CLAIM)

    def test_noise_span_is_not_a_candidate_without_judge(self, monkeypatch):
        auditor = self._auditor()
        auditor.refutation_judge = None
        monkeypatch.setattr(auditor, "_is_heuristic_scorer", lambda: True)  # skip reverse NLI
        assert auditor.confirm_refutations([NOISE_SPAN], BP_CLAIM, [[0.99, 0.0, 0.01]]) == []

    def test_genuine_refutation_survives_without_judge(self, monkeypatch):
        auditor = self._auditor()
        auditor.refutation_judge = None
        monkeypatch.setattr(auditor, "_is_heuristic_scorer", lambda: True)
        assert auditor.confirm_refutations([GENUINE_REFUTATION], BP_CLAIM, [[0.99, 0.0, 0.01]]) == [0]

    def test_judge_path_unchanged(self, monkeypatch):
        """With a judge configured the topical gate alone admits candidates;
        the judge adjudicates (COP30 contract in test_benchmark_fixes)."""
        auditor = self._auditor()
        auditor.refutation_judge = lambda claim, span: True
        monkeypatch.setattr(auditor, "_is_heuristic_scorer", lambda: True)
        assert auditor.confirm_refutations([NOISE_SPAN], BP_CLAIM, [[0.99, 0.0, 0.01]]) == [0]


def test_report_uncertainty_line_is_not_stale():
    """Observed 2026-10-31: report body said "UNCERTAIN" while the run payload
    said EVIDENCE_FOR - _action_synthesize classified AFTER building the text."""
    from odar.engine import ResearchEngine, ResearchOutcome
    from odar.evidence import Claim, EvidenceItem, Relation, Uncertainty
    from odar.llm import ScriptedResearchController

    state = ResearchState(objective=BP_QUESTION)
    src = source(FINDING_SENTENCE)
    state.add_source(src)
    claim = Claim(claim_id=new_id("clm"), text=FINDING_SENTENCE, uncertainty=Uncertainty.UNCERTAIN)
    claim.status = "CERTIFIED"
    claim.provisional = False
    claim.uncertainty = Uncertainty.EVIDENCE_FOR
    state.add_claim(claim)
    state.add_evidence(
        EvidenceItem(
            evidence_id=new_id("ev"),
            claim_id=claim.claim_id,
            source_id=src.source_id,
            span=FINDING_SENTENCE,
            relation=Relation.SUPPORTS,
            entailment_probability=0.99,
            contradiction_probability=0.0,
            relevance_score=1.0,
            circularity_score=0.0,
            evaluated_by="cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8",
        )
    )
    state.uncertainty = Uncertainty.UNCERTAIN  # stale mid-loop value
    outcome = ResearchOutcome(state=state)
    ResearchEngine(model=ScriptedResearchController())._action_synthesize(state, outcome)
    assert "Uncertainty classification: EVIDENCE FOR" in outcome.synthesis
    assert "UNCERTAIN" not in outcome.synthesis
