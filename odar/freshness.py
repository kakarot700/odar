"""Freshness: when was a source published, and is it too old for the claim?

Dates come only from evidence on the page or in its URL (``/2019/05/``,
"Published 12 March 2021", "Last updated: 2024-02-01"). No date found means
"unknown", never "fresh".
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

_URL_DATE = re.compile(r"/(19[9]\d|20[0-4]\d)[/-](0?[1-9]|1[0-2])(?:[/-]|$)")
_URL_YEAR = re.compile(r"/(19[9]\d|20[0-4]\d)/")
_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
_LABELLED = re.compile(
    r"(?:published|posted|updated|last\s+(?:updated|modified|reviewed)|date|dated|reviewed|प्रकाशित|अपडेट)"
    r"\s*(?:on|:)?\s*[:\-]?\s*"
    r"(?:(?:\d{1,2}\s+(?:" + _MONTHS + r")[a-z]*\.?,?\s+(\d{4}))"
    r"|(?:(?:" + _MONTHS + r")[a-z]*\.?\s+\d{1,2},?\s+(\d{4}))"
    r"|(?:(\d{4})-\d{2}-\d{2})"
    r"|(?:\d{1,2}/\d{1,2}/(\d{4})))",
    re.I,
)
_TIME_WORDS = re.compile(
    r"\b(currently|current|now|today|latest|recent(?:ly)?|as of|this year|so far|to date|ongoing|"
    r"most recent|right now|nowadays|still)\b|वर्तमान|अभी|आज|हाल",
    re.I,
)
_CLAIM_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def this_year() -> int:
    return _dt.date.today().year


def source_year(url: str, text: str = "") -> Optional[int]:
    """Best evidence of the source's publication/update year, or None."""
    head = (text or "")[:4000]
    years = [int(next(g for g in m.groups() if g)) for m in _LABELLED.finditer(head)]
    years = [y for y in years if 1990 <= y <= this_year()]
    if years:
        return max(years)  # "updated" beats "published"
    m = _URL_DATE.search(url or "") or _URL_YEAR.search(url or "")
    if m:
        year = int(m.group(1))
        if 1990 <= year <= this_year():
            return year
    return None


def is_time_sensitive(claim: str) -> bool:
    if _TIME_WORDS.search(claim or ""):
        return True
    years = [int(y) for y in _CLAIM_YEAR.findall(claim or "")]
    return any(y >= this_year() - 1 for y in years)


def freshness_warning(claim: str, year: Optional[int], max_age: int = 2) -> str:
    """Plain-language warning when the source looks too old for the claim."""
    if year is None:
        return ""
    claim_years = [int(y) for y in _CLAIM_YEAR.findall(claim or "")]
    later = [y for y in claim_years if y > year]
    if later:
        return f"source dates from {year} but the claim is about {max(later)}"
    if is_time_sensitive(claim) and this_year() - year > max_age:
        return f"time-sensitive claim backed by a source from {year}; check for newer data"
    return ""
