"""Citation precision: every cited source must carry the text it is attached to.

Regressions from the unofficial DeepResearch Bench (DRB) audit of ac3a91c, where 56
of 429 (statement, URL) pairs were judged unsupported:
  * weak corroborators (an NLI match on a topically similar span) were cited
    next to the page the fact came from;
  * a sentence merging facts from different sources cited every source for all
    of it;
  * writer inferences that shared few words with the fact passed NLI;
  * a page's own reference markers ("[2]") in a quoted span were rendered as
    links to unrelated ODAR sources; bibliography lines were mined as claims.
"""

from __future__ import annotations

import math

from odar.citation_auditor import anchors_claim, lexical_coverage
from odar.deep import (
    _keep_markers,
    looks_like_reference_entry,
    split_plan,
    strip_inline_refs,
)
from odar.engine import ResearchEngine
from odar.evidence import Claim, EvidenceItem, Relation, SourceRecord, Uncertainty
from odar.research_state import ResearchState

from tests.unit.test_deep_research import _deep
from tests.unit.test_final_gate import CoverageScorer


class AlwaysEntailed:
    """NLI stub that entails everything (the failure mode lexical anchoring guards)."""

    def predict(self, pairs):
        return [[math.log(0.01), math.log(0.98), math.log(0.01)] for _ in pairs]


def _engine(scorer=None):
    engine, _search, _extractor = _deep()
    engine.auditor.scorer = scorer or CoverageScorer()
    engine.auditor.scorer_backend = "neural-stub"
    return engine


FACTS = [
    "By 2035, one in every three individuals in Japan is expected to be sixty or above.",
    "Japan's total population is estimated at 104,686,000 in 2050, down 17.0% from 2020.",
    "Regional variations are pronounced across the Pacific Rim chub mackerel markets.",
]


# --------------------------------------------------------------------------- #
# lexical anchoring helpers
# --------------------------------------------------------------------------- #
def test_anchors_claim_needs_names_and_numbers_of_the_claim():
    claim = (
        "South Korea operates a commercially important chub mackerel fishery in the Northwest Pacific region."
    )
    on_topic = (
        "Chub mackerel is a commercially important fishery species in the Northwest Pacific region, "
        "operated by several fleets."
    )
    assert lexical_coverage(claim, on_topic) >= 0.6  # topically similar ...
    assert not anchors_claim(claim, on_topic)  # ... but never says Korea
    assert anchors_claim(
        claim,
        "In South Korea a commercially important chub mackerel fishery operates in the Northwest Pacific region.",
    )
    assert not anchors_claim(
        "Revenue reached 450.20 billion in 2032.", "Revenue reached 230.76 billion in 2032."
    )
    assert anchors_claim(
        "Revenue reached 450.20 billion in 2032.", "The market revenue reached 450.20 billion by 2032."
    )


def test_lexical_coverage_folds_plurals():
    assert (
        lexical_coverage("Prebiotics modulate microbiota", "A prebiotic can modulate the microbiota") == 1.0
    )


def test_strip_inline_refs_and_reference_entries():
    assert (
        strip_inline_refs("which are referred to as probiotics [15].")
        == "which are referred to as probiotics."
    )
    assert strip_inline_refs("other countries include China and Russia [7,8].") == (
        "other countries include China and Russia."
    )
    for entry in (
        "Miller PE, Lesko SM, Muscat JE, et al. Dietary patterns and colorectal adenoma and cancer risk.",
        "Fournier DA, Hampton J, Sibert JR (1998) MULTIFAN-CL: a length-based, age-structured model.",
        "Kaplan, T., Zamir, S.: Asymmetric first-price auctions with uniform distributions.",
        "2019. Effects of water temperature and prey density on recent growth of chub mackerel larvae.",
        "Influence of sea surface temperature on chub mackerel. Journal of Shanghai Ocean University, 20(1): 102-107.",
    ):
        assert looks_like_reference_entry(entry), entry
    for sentence in (
        "South Korea operates a commercially important chub mackerel fishery.",
        "Maskin and Riley (Games Econ Behav 45:395-409, 2003) prove that the equilibrium is unique.",
        "The US EU agreement was signed in 2019 by both parties.",
        "In 2025, survey responses showed that about two in five districts prohibited GenAI tools.",
    ):
        assert not looks_like_reference_entry(sentence), sentence


def test_keep_markers_prunes_only_unbacking_citations():
    assert _keep_markers("Alpha is one [1][2].", [0]) == "Alpha is one [1]."
    assert _keep_markers("Alpha [1], and beta [2].", [1]) == "Alpha, and beta [2]."
    assert _keep_markers("Alpha is one [1][2].", [0, 1]) == "Alpha is one [1][2]."


# --------------------------------------------------------------------------- #
# writer gates
# --------------------------------------------------------------------------- #
def test_merged_multi_source_sentence_is_split_one_citation_each():
    engine = _engine()
    merged = (
        "Japan's total population is estimated at 104,686,000 in 2050, down 17.0% from 2020; "
        "by 2035, one in every three individuals in Japan is expected to be sixty or above [2][1]."
    )
    (plan,) = engine._citation_plans([merged], [[1, 0]], FACTS)
    assert plan is not None
    pieces = split_plan(plan)
    assert len(pieces) == 2
    assert pieces[0].endswith("[2].") and "[1]" not in pieces[0] and "104,686,000" in pieces[0]
    assert pieces[1].startswith("By 2035") and pieces[1].endswith("[1].") and "[2]" not in pieces[1]


def test_sentence_entailed_by_one_fact_drops_the_other_citation():
    engine = _engine()
    sentence = "By 2035, one in every three individuals in Japan is expected to be sixty or above [1][3]."
    (plan,) = engine._citation_plans([sentence], [[0, 2]], FACTS)
    assert plan == "By 2035, one in every three individuals in Japan is expected to be sixty or above [1]."


def test_inference_with_little_fact_wording_fails_even_when_nli_entails():
    engine = _engine(AlwaysEntailed())
    plans = engine._citation_plans(
        [
            "This tiered structure appears in specific market offerings [3].",
            "Regional variations are pronounced across Pacific Rim mackerel markets [3].",
        ],
        [[2], [2]],
        FACTS,
    )
    assert plans[0] is None
    assert plans[1] is not None


def test_meta_sentences_about_the_facts_fail():
    engine = _engine(AlwaysEntailed())
    sentence = "Regional variations are the principal forces described in the certified facts [3]."
    assert engine._citation_plans([sentence], [[2]], FACTS) == [None]


def test_final_gate_ships_split_sentences_and_drops_repeats():
    engine = _engine()
    state = ResearchState(objective="How is Japan's population changing?")
    merged = (
        "Japan's total population is estimated at 104,686,000 in 2050, down 17.0% from 2020; "
        "by 2035, one in every three individuals in Japan is expected to be sixty or above [2][1]."
    )
    text = f"{FACTS[0].rstrip('.')} [1]. {merged}"
    out = engine._final_gate(text, FACTS, state)
    assert out.count("By 2035") == 1  # the split clause repeated the first sentence
    assert "104,686,000 in 2050, down 17.0% from 2020 [2]." in out
    assert "[2][1]" not in out


# --------------------------------------------------------------------------- #
# claim -> source attachment
# --------------------------------------------------------------------------- #
def test_claim_sources_cite_corroborators_only_when_their_span_carries_the_claim():
    state = ResearchState(objective="chub mackerel fisheries")
    claim = Claim(
        claim_id="clm_1",
        text="South Korea operates a commercially important chub mackerel fishery in the Northwest Pacific region.",
        uncertainty=Uncertainty.EVIDENCE_FOR,
    )
    state.add_claim(claim)
    spans = {
        "src_origin": claim.text,
        "src_weak": "Chub mackerel is a commercially important fishery species in the Northwest Pacific region.",
        "src_good": "South Korea operates a commercially important fishery for chub mackerel in the Northwest Pacific region.",
    }
    for n, (sid, span) in enumerate(spans.items()):
        state.add_source(SourceRecord(source_id=sid, url=f"https://{sid}.example/x"))
        state.add_evidence(
            EvidenceItem(
                evidence_id=f"ev_{n}",
                claim_id=claim.claim_id,
                source_id=sid,
                span=span,
                relation=Relation.SUPPORTS,
                entailment_probability=0.99 if sid == "src_weak" else 0.95,
                contradiction_probability=0.0,
                relevance_score=1.0,
            )
        )
    mapping = ResearchEngine._claim_sources(state, [claim])
    assert mapping["clm_1"][0] == "src_origin"  # best-anchored first, not highest NLI
    assert "src_good" in mapping["clm_1"]
    assert "src_weak" not in mapping["clm_1"]


def _conflict_state(claim_text: str, refute_span: str) -> ResearchState:
    state = ResearchState(objective="chub mackerel growth")
    claim = Claim(
        claim_id="clm_c", text=claim_text, uncertainty=Uncertainty.CONFLICTING_EVIDENCE, status="CONFLICTING"
    )
    state.add_claim(claim)
    for sid in ("src_a", "src_b"):
        state.add_source(SourceRecord(source_id=sid, url=f"https://{sid}.example/x"))
    for n, (sid, span, rel) in enumerate(
        [("src_a", claim_text, Relation.SUPPORTS), ("src_b", refute_span, Relation.REFUTES)]
    ):
        state.add_evidence(
            EvidenceItem(
                evidence_id=f"ev_c{n}",
                claim_id="clm_c",
                source_id=sid,
                span=span,
                relation=rel,
                entailment_probability=0.9 if rel is Relation.SUPPORTS else 0.0,
                contradiction_probability=0.0 if rel is Relation.SUPPORTS else 0.9,
                relevance_score=1.0,
            )
        )
    state.contradictions.append({"claim_id": "clm_c", "examined": False})
    return state


def test_disagreement_block_gives_each_side_its_own_sentence_and_citation():
    engine = _engine()
    state = _conflict_state(
        "The Von Bertalanffy growth equation was used to describe the growth status of chub mackerel.",
        "Ryuki et al. [2] used the empirical formula of Pope [1] to infer the catches of chub mackerel.",
    )
    numbers: dict = {}
    lines = engine._disagreement_block(state, numbers)
    text = "\n".join(lines)
    assert "**" not in text
    assert (
        "- Claim: The Von Bertalanffy growth equation was used to describe the growth status of chub mackerel. [[1]](https://src_a.example/x)"
        in text
    )
    assert (
        'Contradicted by: "Ryuki et al. used the empirical formula of Pope to infer the catches of chub mackerel." [[2]](https://src_b.example/x)'
        in text
    )
    assert "[2]" not in text.replace("[[2]]", "")  # the page's own reference markers are gone


def test_disagreement_block_skips_bibliography_pairs():
    engine = _engine()
    state = _conflict_state(
        "Fournier DA, Hampton J, Sibert JR (1998) MULTIFAN-CL: a length-based model.",
        "2019. Effects of water temperature and prey density on recent growth of chub mackerel larvae.",
    )
    assert engine._disagreement_block(state, {}) == []
