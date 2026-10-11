"""Final report gate: a sentence that merges several cited findings (or a
summary/conclusion sentence) ships only when each of its claims is entailed by
its OWN cited fact; the joined premise of all cited facts is never enough.

Regression: after3 q1 shipped "The formal outcomes therefore combined the
overarching political text, adaptation governance, land rights, and initial
decisions on trade and climate into a single package ..." [4][1][2] with
P(entailment) 0.83 against the joined facts but <= 0.002 against each one."""

from __future__ import annotations

import math

from odar.deep import claim_units, cited_fact_indices, strip_markers
from odar.research_state import ResearchState

from tests.unit.test_deep_research import _deep


class CoverageScorer:
    """Entailed when >= 80% of the hypothesis words occur in the premise; so a
    merged sentence is 'entailed' by the joined facts but by no single fact -
    exactly how the real cross-encoder behaved on the q1 sentence."""

    def __init__(self):
        self.pairs = []

    def predict(self, pairs):
        rows = []
        for premise, hypothesis in pairs:
            self.pairs.append((premise, hypothesis))
            hyp = {w.strip(".,;:").lower() for w in hypothesis.split()} - {"", "and", "the", "a", "while"}
            prem = {w.strip(".,;:").lower() for w in premise.split()}
            covered = len(hyp & prem) >= 0.8 * max(1, len(hyp))
            probs = [0.01, 0.97, 0.02] if covered else [0.01, 0.01, 0.98]
            rows.append([math.log(x) for x in probs])
        return rows


FACTS = [
    "The Mutirao decision included dialogues on trade and climate.",
    "Fifteen governments launched a land tenure commitment for Indigenous Peoples.",
    "Some Parties raised objections to the adaptation indicators in the final plenary.",
]
MERGED = (
    "Therefore the Mutirao decision included dialogues on trade, fifteen governments launched a land tenure "
    "commitment and some Parties raised objections in the final plenary [1][2][3]."
)


def cite(fact: str, n: int) -> str:
    return f"{fact.rstrip('.')} [{n}]."


def _engine():
    engine, _search, _extractor = _deep()
    scorer = CoverageScorer()
    engine.auditor.scorer = scorer
    engine.auditor.scorer_backend = "neural-stub"
    return engine, scorer


def _state():
    return ResearchState(objective="What were the outcomes of COP30?")


def test_claim_units_split_clauses_not_noun_lists():
    units = claim_units(
        "Parties adopted the indicators; some Parties strongly objected, while others welcomed the text"
    )
    assert units == [
        "Parties adopted the indicators",
        "some Parties strongly objected",
        "others welcomed the text",
    ]
    # a plain list of nouns is one claim
    assert claim_units("The package covered finance, adaptation, trade and land rights") == [
        "The package covered finance, adaptation, trade and land rights"
    ]
    # tiny fragments are merged, never scored alone
    assert len(claim_units("It passed; finally")) == 1


def test_marker_helpers():
    assert cited_fact_indices("A [2][1][2] and [9].", 3) == [1, 0]
    assert strip_markers("Alpha is one [1][2].") == "Alpha is one."


def test_merged_sentence_entailed_only_by_joined_facts_is_rejected():
    engine, _scorer = _engine()
    joined = " ".join(FACTS)
    # the old rule (joined premise) would have let it through
    assert engine.auditor.entailment_scores([(joined, strip_markers(MERGED))])[0] >= 0.5
    assert engine._entailed_flags([MERGED], [[0, 1, 2]], FACTS) == [False]


def test_multi_citation_sentence_passes_when_each_claim_has_its_own_fact():
    engine, _scorer = _engine()
    sentence = (
        "The Mutirao decision included dialogues on trade and climate, while fifteen governments "
        "launched a land tenure commitment for Indigenous Peoples [1][2]."
    )
    assert engine._entailed_flags([sentence], [[0, 1]], FACTS) == [True]
    # one cited fact entailing the whole sentence is enough too
    single = "Some Parties raised objections to the adaptation indicators in the final plenary [3][1]."
    assert engine._entailed_flags([single], [[2, 0]], FACTS) == [True]


def test_uncited_or_unentailed_single_sentences_fail():
    engine, _scorer = _engine()
    assert engine._entailed_flags(["The summit was a triumph."], [[]], FACTS) == [False]
    assert engine._entailed_flags(["The summit was a triumph [1]."], [[0]], FACTS) == [False]
    assert engine._entailed_flags([cite(FACTS[0], 1)], [[0]], FACTS) == [True]


def test_final_gate_drops_merged_summary_and_conclusion_sentences():
    engine, _scorer = _engine()
    state = _state()
    summary = f"{cite(FACTS[0], 1)} {cite(FACTS[1], 2)}\n\n{MERGED}"
    out = engine._final_gate(summary, FACTS, state)
    assert "Therefore the Mutirao" not in out
    assert cite(FACTS[0], 1) in out and cite(FACTS[1], 2) in out
    assert engine.telemetry.counters.get("final_gate_sentences_dropped") == 1
    assert any("final report gate" in n for n in state.notes)


def test_writer_gate_queues_merged_sentence_for_rewrite_not_ship():
    engine, _scorer = _engine()
    state = _state()
    pending = []
    prose = f"{cite(FACTS[2], 3)} {MERGED}"
    out = engine._entailment_gate(prose, FACTS, state, pending)
    assert "Therefore the Mutirao" not in out
    assert len(pending) == 1 and pending[0][1] == [0, 1, 2]


def test_nli_scores_are_cached_across_gates():
    engine, scorer = _engine()
    sentence = cite(FACTS[0], 1)
    engine._entailed_flags([sentence], [[0]], FACTS)
    seen = len(scorer.pairs)
    engine._entailed_flags([sentence], [[0]], FACTS)
    assert len(scorer.pairs) == seen


def test_gate_sentences_keep_trailing_markers_with_their_sentence():
    from odar.deep import gate_sentences

    rows = gate_sentences("Fact one is here. [2] Next sentence cites [1].\n\nOther paragraph [3].")
    assert rows == [
        (0, "Fact one is here. [2]"),
        (0, "Next sentence cites [1]."),
        (1, "Other paragraph [3]."),
    ]
