"""Structured quantitative evidence extraction.

Generic number-plus-unit regexes conflate sample sizes with effect sizes and
percentages with durations.  This module extracts *typed* statistical facts
with context preserved:

    sample_size, percentage, mean, median, sd, confidence_interval,
    p_value, odds_ratio, risk_ratio, hazard_ratio, effect_size,
    duration, quantity

Each :class:`StatisticalFact` keeps the exact source span, unit and
directionality so downstream synthesis can check numerical consistency
instead of trusting a loose aggregate.

Design note: extraction is pattern-based but *context-anchored* (a number is
classified by the surrounding statistical vocabulary, not by shape alone),
and ambiguous statements are tagged ``ambiguous=True`` instead of being
silently guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

_NUM = r"(\d+(?:[.,]\d+)?)"

_PATTERNS = {
    # n = 1,024 participants / sample of 390
    "sample_size": [
        re.compile(rf"\bn\s*[=:]\s*{_NUM}\b", re.I),
        re.compile(rf"\b(?:sample|cohort)\s+of\s+{_NUM}\b", re.I),
        re.compile(rf"{_NUM}\s+(?:participants|subjects|patients|adults|women|men|people)\b", re.I),
    ],
    # p = 0.03 / p < 0.001
    "p_value": [
        re.compile(rf"\bp\s*[=:]\s*{_NUM}\b", re.I),
        re.compile(rf"\bp\s*[<>]=?\s*{_NUM}\b", re.I),
    ],
    # 95% CI 0.4 to 0.9 / 95% CI [1.2, 3.4]
    "confidence_interval": [
        re.compile(
            rf"\b\d{{1,2}}(?:\.\d)?%?\s*(?:CI|confidence interval)\s*(?:of|:)?\s*"
            rf"(?:\[|\()?{_NUM}\s*(?:,|to|-|–)\s*{_NUM}(?:\]|\))?",
            re.I,
        ),
    ],
    # odds ratio 0.75 / OR 0.8 / risk ratio 1.2 / RR / hazard ratio
    "odds_ratio": [re.compile(rf"\b(?:odds\s+ratio|OR)\b[^.\n]{{0,40}}?{_NUM}", re.I)],
    "risk_ratio": [re.compile(rf"\b(?:risk\s+ratio|RR|relative\s+risk)\b[^.\n]{{0,40}}?{_NUM}", re.I)],
    "hazard_ratio": [re.compile(rf"\b(?:hazard\s+ratio|HR)\b[^.\n]{{0,40}}?{_NUM}", re.I)],
    # mean 5.2 kg / mean difference of 0.9
    "mean": [
        re.compile(
            rf"\bmean(?:\s+(?:difference|change|loss|gain|weight))?\s+(?:of\s+|was\s+)?{_NUM}\s*(kg|lbs?|pounds?|%|points?|mmol\w*)?",
            re.I,
        ),
    ],
    "median": [
        re.compile(rf"\bmedian\s+(?:of\s+|was\s+)?{_NUM}\s*(kg|lbs?|pounds?|%|weeks?|months?)?", re.I)
    ],
    "sd": [
        re.compile(rf"\b(?:SD|standard deviation|±|\+/-)\s*{_NUM}", re.I),
        re.compile(rf"{_NUM}\s*(?:±|\+/-)\s*{_NUM}", re.I),
    ],
    # effect size / Cohen's d
    "effect_size": [re.compile(rf"\b(?:effect\s+size|cohen'?s?\s*d|d)\s*[=:]\s*{_NUM}", re.I)],
    # percentages with context
    "percentage": [
        re.compile(
            rf"{_NUM}\s*(?:%|percent(?:age)?)(?:\s+(?:more|less|greater|lower|higher|reduction|increase|decrease|loss))?",
            re.I,
        )
    ],
    # durations
    "duration": [re.compile(rf"{_NUM}\s*(weeks?|months?|years?|days?)\b", re.I)],
    # generic measured quantities (kg, kcal...)
    "quantity": [re.compile(rf"{_NUM}\s*(kg|kilograms?|kcal|calories?|lbs?|pounds?|mm\s*Hg|bpm)\b", re.I)],
}

_DIRECTION_HINTS = {
    "reduction": "decreases",
    "decrease": "decreases",
    "lower": "decreases",
    "less": "decreases",
    "loss": "decreases",
    "increase": "increases",
    "greater": "increases",
    "higher": "increases",
    "more": "increases",
    "gain": "increases",
}


@dataclass
class StatisticalFact:
    kind: str
    value: float
    unit: str = ""
    raw: str = ""
    directionality: str = "unspecified"
    context: str = ""
    ambiguous: bool = False

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "value": self.value,
            "unit": self.unit,
            "raw": self.raw[:160],
            "directionality": self.directionality,
            "context": self.context[:200],
            "ambiguous": self.ambiguous,
        }


def _parse_number(text: str) -> Optional[float]:
    try:
        return float(text.replace(",", "").replace(" ", ""))
    except (ValueError, AttributeError):
        return None


def _sentence_window(text: str, start: int, end: int) -> str:
    lo = max(0, start - 60)
    hi = min(len(text), end + 60)
    return text[lo:hi].replace("\n", " ")


def _directionality(raw: str) -> str:
    lowered = raw.lower()
    for hint, direction in _DIRECTION_HINTS.items():
        if hint in lowered:
            return direction
    return "unspecified"


_BARE_NUMBER_RE = re.compile(r"\b(\d{1,7}(?:\.\d+)?)\b")
_YEAR_MIN, _YEAR_MAX = 1800, 2100


def extract_statistical_facts(text: str) -> List[StatisticalFact]:
    """Extract typed statistical facts from a passage.

    Numbers that match no typed pattern are still surfaced as
    ``kind="unclassified"`` facts flagged ``ambiguous=True`` - a bare number
    without a statistical role must never be silently treated as a fact.
    """
    facts: List[StatisticalFact] = []
    if not text:
        return facts
    seen_spans = set()
    matched_ranges: List[tuple] = []
    for kind, patterns in _PATTERNS.items():
        for pattern in patterns:
            for match in pattern.finditer(text):
                matched_ranges.append((match.start(), match.end()))
                raw = match.group(0).strip()
                span_key = (kind, match.start() // 40, raw)
                if span_key in seen_spans:
                    continue
                seen_spans.add(span_key)
                value = None
                unit = ""
                groups = [g for g in match.groups() if g is not None]
                if kind == "confidence_interval" and len(groups) >= 2:
                    low = _parse_number(groups[0])
                    high = _parse_number(groups[1])
                    if low is None or high is None:
                        continue
                    facts.append(
                        StatisticalFact(
                            kind=kind,
                            value=round((low + high) / 2, 4),
                            unit=f"CI[{low}..{high}]",
                            raw=raw,
                            directionality=_directionality(raw),
                            context=_sentence_window(text, match.start(), match.end()),
                        )
                    )
                    continue
                if groups:
                    value = _parse_number(groups[0])
                if value is None:
                    continue
                if (
                    kind in ("percentage", "duration", "quantity", "mean", "median", "sd")
                    and len(groups) > 1
                    and groups[-1]
                ):
                    unit = str(groups[-1]).lower()
                ambiguous = False
                if kind == "quantity" and not unit:
                    ambiguous = True
                if kind == "sample_size" and value < 2:
                    continue
                facts.append(
                    StatisticalFact(
                        kind=kind,
                        value=value,
                        unit=unit,
                        raw=raw,
                        directionality=_directionality(raw),
                        context=_sentence_window(text, match.start(), match.end()),
                        ambiguous=ambiguous,
                    )
                )
    # Bare numbers with no statistical role: surfaced as ambiguous, never lost.
    for match in _BARE_NUMBER_RE.finditer(text):
        start, end = match.span()
        if any(not (end <= m_start or start >= m_end) for m_start, m_end in matched_ranges):
            continue  # already part of a typed fact
        value = _parse_number(match.group(1))
        if value is None:
            continue
        if len(match.group(1)) == 4 and _YEAR_MIN <= value <= _YEAR_MAX:
            continue  # year reference
        facts.append(
            StatisticalFact(
                kind="unclassified",
                value=value,
                unit="",
                raw=match.group(0),
                directionality="unspecified",
                context=_sentence_window(text, start, end),
                ambiguous=True,
            )
        )
    facts.sort(key=lambda f: (f.kind, f.value))
    return facts


def summarize_facts(facts: List[StatisticalFact]) -> dict:
    """Compact digest used by reports and numerical-consistency checks."""
    by_kind: dict = {}
    for fact in facts:
        by_kind.setdefault(fact.kind, []).append(fact.value)
    return {
        "count": len(facts),
        "kinds": {k: len(v) for k, v in sorted(by_kind.items())},
        "sample_sizes": sorted(by_kind.get("sample_size", [])),
        "p_values": sorted(by_kind.get("p_value", [])),
        "ambiguous_count": sum(1 for f in facts if f.ambiguous),
    }
