"""Language + follow-up helpers on the free model routes.

* ``detect_language`` - script-based (Devanagari => Hindi), no model needed
* ``translate`` - keeps URLs and ``[n]`` citation markers byte-for-byte; if a
  translation drops a marker or URL the original text is returned instead
* ``answer_followup`` - answers a question about a finished report using only
  that report; abstains ("The report doesn't cover that") when it can't, and
  drops sentences with numbers that are not in the report
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

Complete = Callable[[str, str, int], str]  # (system, prompt, max_tokens) -> text

LANGUAGES = {"en": "English", "hi": "Hindi"}
ABSTAIN = "The report doesn't cover that."

_URL = re.compile(r"https?://[^\s)\]>\"']+")
_MARKER = re.compile(r"\[\d{1,3}\]")
_NUMBER = re.compile(r"\d[\d,.]*\d|\d")


def make_complete(role: str = "writer", max_calls: int = 12) -> Optional[Complete]:
    """A completion function on ODAR's free-model router, or None without a key."""
    try:
        from odar.budget import Budget, Governor
        from odar.router import AnthropicMessagesClient, ModelRouter, load_routes

        client = AnthropicMessagesClient(base_url=os.environ.get("ODAR_ANTHROPIC_BASE_URL") or None)
        router = ModelRouter(
            client=client,
            governor=Governor(
                Budget(max_model_calls=max_calls * 3, max_retries_per_call=3, max_wall_clock_s=240)
            ),
            routes=load_routes(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("LLM unavailable: %s", exc)
        return None

    def complete(system: str, prompt: str, max_tokens: int = 1500) -> str:
        return router.complete(role, prompt, system, max_tokens=max_tokens)

    return complete


def detect_language(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    devanagari = sum(1 for c in letters if "\u0900" <= c <= "\u097f")
    if devanagari / len(letters) >= 0.3:
        return "hi"
    latin = sum(1 for c in letters if c.isascii())
    return "en" if latin / len(letters) >= 0.7 else "other"


def _protect(text: str) -> tuple[str, List[str]]:
    urls: List[str] = []

    def keep(m: "re.Match[str]") -> str:
        urls.append(m.group(0))
        return f"⟦U{len(urls)}⟧"

    return _URL.sub(keep, text), urls


def _restore(text: str, urls: List[str]) -> str:
    for i, url in enumerate(urls, 1):
        text = text.replace(f"⟦U{i}⟧", url)
    return text


def _chunks(text: str, size: int = 2500) -> List[str]:
    out, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) > size and cur:
            out.append(cur)
            cur = ""
        cur += para + "\n"
    if cur.strip():
        out.append(cur)
    return out or [text]


def translate(text: str, target: str, complete: Optional[Complete]) -> tuple[str, bool]:
    """Returns (text, translated?). Citations and URLs are verified to survive."""
    if complete is None or not text.strip() or target not in LANGUAGES:
        return text, False
    lang = LANGUAGES[target]
    system = (
        f"Translate the user's text into {lang}. Keep Markdown structure, every citation marker like [3], "
        "every placeholder like ⟦U1⟧, numbers and proper nouns exactly as they are. "
        "Output only the translation, nothing else."
    )
    pieces = []
    for chunk in _chunks(text):
        protected, urls = _protect(chunk)
        try:
            out = complete(system, protected, 3000).strip()
        except Exception as exc:  # noqa: BLE001
            logger.info("translation failed: %s", exc)
            return text, False
        restored = _restore(out, urls)
        lost_markers = set(_MARKER.findall(chunk)) - set(_MARKER.findall(restored))
        lost_urls = [u for u in urls if u not in restored]
        if not out or lost_markers or lost_urls:
            logger.info("translation dropped markers/urls; keeping original")
            return text, False
        pieces.append(restored)
    return "\n".join(p.rstrip("\n") for p in pieces), True


# ---------------------------------------------------------------------- #
# Follow-ups
# ---------------------------------------------------------------------- #
def report_context(mode: str, result: Dict[str, Any], limit: int = 12000) -> str:
    if mode == "research":
        text = result.get("synthesis") or result.get("answer") or ""
        check = result.get("citation_check") or {}
        if check:
            text += "\n\nCitation check:\n" + _check_context(check)
    elif mode == "check":
        text = _check_context(result)
    else:
        lines = [
            f"- {r['status']}: {r['raw']} {'; '.join(r.get('problems') or [])}"
            for r in result.get("references", [])
        ]
        text = (
            "Reference check:\n"
            + "\n".join(lines)
            + "\n\nBibliography:\n"
            + "\n".join(result.get("bibliography", []))
        )
    return text[:limit]


def _check_context(result: Dict[str, Any]) -> str:
    lines = [f"Trust score: {result.get('trust_score')} ({result.get('grade')})"]
    for i, c in enumerate(result.get("claims", []), 1):
        lines.append(f"[{i}] Claim: {c['claim']} -> {c['verdict']}")
        for cit in c.get("citations", []):
            if cit.get("url"):
                lines.append(f"    source {cit['url']} ({cit.get('source_type') or 'web'}): {cit['verdict']}")
                if cit.get("quote"):
                    lines.append(f'    quote: "{cit["quote"][:400]}"')
        if c.get("replacement"):
            lines.append(f"    suggested source: {c['replacement']['url']}")
    return "\n".join(lines)


FOLLOWUP_SYSTEM = (
    "You answer questions about a research or citation-check report. Use ONLY the report below. "
    "Cite the report's markers like [2] where they apply. If the report does not contain the answer, reply "
    f'exactly "{ABSTAIN}" and nothing else. Never invent facts, numbers, or sources. '
    "Do not write essays or assignments for the user; explain what the report found. "
    "Answer in {lang}, in at most 150 words."
)


def answer_followup(
    mode: str, result: Dict[str, Any], question: str, complete: Optional[Complete], language: str = "en"
) -> Dict[str, Any]:
    if complete is None:
        return {"answer": "", "abstained": True, "error": "no model route available"}
    context = report_context(mode, result)
    system = FOLLOWUP_SYSTEM.replace("{lang}", LANGUAGES.get(language, "English"))
    raw = complete(system, f"REPORT:\n{context}\n\nQUESTION: {question.strip()[:500]}", 600).strip()
    if not raw or ABSTAIN.lower().rstrip(".") in raw.lower():
        return {"answer": ABSTAIN, "abstained": True, "dropped": 0}
    # Grounding: drop sentences whose numbers never appear in the report.
    known = {n.replace(",", "") for n in _NUMBER.findall(context + " " + question)}
    kept, dropped = [], 0
    for sentence in re.split(r"(?<=[.!?।])\s+", raw):
        nums = {n.replace(",", "") for n in _NUMBER.findall(_MARKER.sub("", sentence))}
        if nums - known:
            dropped += 1
            continue
        kept.append(sentence)
    answer = " ".join(kept).strip()
    if not answer:
        return {"answer": ABSTAIN, "abstained": True, "dropped": dropped}
    return {"answer": answer, "abstained": False, "dropped": dropped}
