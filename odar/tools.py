"""The single governed execution boundary (P0 invariant).

ALL side-effecting capabilities in the production path execute through
:class:`GovernedExecutor`.  There is exactly one place where budgets are
approved, cancellation is honored, retries are classified and security
policy applies.  Neither the engine's action handlers nor the LLM tool-use
loop may touch retrieval / fetch / sandbox / evaluation directly: they call
this executor.

Every method:

1. checks cancellation (token);
2. obtains the Governor's approval for the specific resource;
3. executes the capability;
4. classifies failures through the retry policy (bounded, never blanket);
5. records telemetry.

Anything that bypasses this module in production code is an architecture
violation and is covered by regression tests.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from odar.budget import BudgetExceeded, Governor
from odar.citation_auditor import CitationAuditor
from odar.evidence import Claim, Relation, SourceRecord
from odar.loop_breaker import ACTION_BLOCK, SemanticLoopBreaker
from odar.retry import RetryPolicy, classify_exception
from odar.sandbox import ExecutionSandbox
from odar.schemas import SearchHit
from odar.source_quality import classify_source, rank_sources
from odar.telemetry import TelemetryRecorder
from odar.trust import sanitize_external_text, scan_for_injection

logger = logging.getLogger(__name__)


@dataclass
class FetchOutcome:
    """Result of a governed fetch (never raises; policy outcomes are data)."""

    source: Optional[SourceRecord] = None
    quarantined: bool = False
    denied: str = ""
    error: str = ""


@dataclass
class DialecticOutcome:
    verdict: str = "UNDECIDED"
    counter_queries: List[str] = field(default_factory=list)
    semantic_relations: List[Dict[str, Any]] = field(default_factory=list)
    cue_diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    denied: str = ""


class GovernedExecutor:
    """The one authoritative boundary between reasoning and side effects."""

    def __init__(
        self,
        governor: Governor,
        search: Any,
        extractor: Any,
        sandbox: Optional[ExecutionSandbox] = None,
        auditor: Optional[CitationAuditor] = None,
        loop_breaker: Optional[SemanticLoopBreaker] = None,
        retry_policy: Optional[RetryPolicy] = None,
        telemetry: Optional[TelemetryRecorder] = None,
        token: Optional[Any] = None,
    ) -> None:
        self.governor = governor
        self.search_backend = search
        self.extractor = extractor
        self.sandbox = sandbox or ExecutionSandbox()
        self.auditor = auditor
        self.loop_breaker = loop_breaker or SemanticLoopBreaker()
        # Default retry policy inherits its bound from the budget itself, so
        # retries can never exceed the configured allowance.
        self.retry_policy = retry_policy or RetryPolicy(
            max_retries=governor.budget.max_retries_per_call,
            governor=governor,
        )
        self.telemetry = telemetry or TelemetryRecorder()
        self.token = token

    # ------------------------------------------------------------------ #
    def _check_cancel(self) -> None:
        if self.token is not None:
            self.token.check()

    # ================================================================== #
    # SEARCH - exactly one budget unit per actual network search
    # ================================================================== #
    def search(self, query: str, max_results: int = 6) -> List[SearchHit]:
        """Governed search.  Raises BudgetExceeded when denied; the caller
        decides how to represent the denial.  One approval == one call."""
        self._check_cancel()
        self.governor.approve_search()  # BEFORE any side effect
        guard = self.loop_breaker.guard(query)
        if guard.action == ACTION_BLOCK:
            self.telemetry.count("loop_breaker_blocks")
            return []
        if guard.intercepted:
            self.telemetry.count("loop_breaker_reroutes")
            query = guard.query
        started = time.perf_counter()
        attempt = 0
        while True:
            self._check_cancel()
            try:
                hits = self.search_backend.text(query, max_results=max_results) or []
                self.telemetry.count("search_calls")
                self.telemetry.latency("search", time.perf_counter() - started)
                return list(hits)
            except BudgetExceeded:
                raise
            except Exception as exc:
                failure = classify_exception(exc)
                decision = self.retry_policy.decide(failure, attempt)
                if not decision.should_retry:
                    self.telemetry.count("search_failed")
                    logger.warning("search failed permanently (%s): %s", failure.value, exc)
                    return []
                try:
                    self.governor.approve_retry()
                except BudgetExceeded:
                    self.telemetry.count("search_failed")
                    return []
                attempt += 1
                self.telemetry.count("search_retries")
                time.sleep(min(decision.delay_s, 2.0))

    # ================================================================== #
    # FETCH - governed page retrieval with SSRF + injection policy
    # ================================================================== #
    def fetch(self, url: str, objective: str, fetched_urls: List[str]) -> FetchOutcome:
        self._check_cancel()
        url = (url or "").strip()
        if not url:
            return FetchOutcome(error="empty url")
        if url in fetched_urls:
            self.telemetry.count("duplicate_fetch_suppressed")
            return FetchOutcome(denied="duplicate fetch suppressed")
        self.governor.approve_fetch()  # BEFORE any side effect
        fetched_urls.append(url)
        started = time.perf_counter()
        page = self.extractor.extract(url)
        self.telemetry.count("fetch_calls")
        self.telemetry.latency("fetch", time.perf_counter() - started)
        if page.quarantined:
            self.telemetry.count("injection_quarantined")
            return FetchOutcome(quarantined=True)
        # Defense-in-depth: the governed boundary NEVER trusts an extractor's
        # silence.  Every fetched body is re-scanned here; a positive scan
        # quarantines the page regardless of backend.
        if page.ok and page.text:
            scan = scan_for_injection(page.text)
            if not scan.clean:
                self.telemetry.count("injection_quarantined")
                logger.warning(
                    "fetch quarantined %s at boundary: %s",
                    url,
                    [f["category"] for f in scan.findings],
                )
                return FetchOutcome(quarantined=True)
        if not page.ok or page.chars < 200:
            self.telemetry.count("fetch_failed")
            return FetchOutcome(error=page.error or "thin content")
        record = SourceRecord(
            source_id=_new_id("src"),
            url=page.url,
            resolved_url=page.resolved_url,
            title=sanitize_external_text(page.title, max_chars=200),
            publisher_class=classify_source(page.resolved_url or page.url),
            retrieved_at=time.time(),
            http_status=page.http_status,
            content_hash=page.content_hash,
            content_type=page.content_type,
            content_chars=len(page.text),
            extracted_text=page.text,
        )
        ranked = rank_sources(
            objective, [SearchHit(url=page.url, title=page.title, snippet="", engine="fetch")]
        )
        record.independent = bool(ranked) and ranked[0].url == page.url
        return FetchOutcome(source=record)

    # ================================================================== #
    # SANDBOX - governed code execution
    # ================================================================== #
    def run_python(self, code: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        self._check_cancel()
        self.governor.approve_sandbox()
        started = time.perf_counter()
        digest = self.sandbox.run_python(code, timeout=timeout)
        self.telemetry.count("sandbox_executions")
        self.telemetry.latency("sandbox", time.perf_counter() - started)
        return digest

    # ================================================================== #
    # EVIDENCE EVALUATION - semantic, provenance-aware, budgeted
    # ================================================================== #
    def evaluate_claim(self, claim: Claim, sources: List[SourceRecord], texts: List[str]):
        self._check_cancel()
        self.governor.approve_verification()
        if self.auditor is None:
            raise RuntimeError("executor has no auditor configured")
        started = time.perf_counter()
        result, items, gaps = self.auditor.audit_with_provenance(claim, sources, texts)
        self.telemetry.count("evidence_evaluations")
        self.telemetry.latency("evidence_eval", time.perf_counter() - started)
        return result, items, gaps

    # ================================================================== #
    # DIALECTIC - governed counter-search (every query is one budget unit)
    # ================================================================== #
    def dialectic(
        self,
        hypothesis: str,
        evaluator: Any,
        existing_evidence: Optional[List[str]] = None,
        max_counter_queries: int = 2,
    ) -> DialecticOutcome:
        """Counter-evidence search through the GOVERNED search path.

        Budget invariant: ``max_counter_queries`` approvals are consumed at
        most, one per actual network search.  Verdict authority is the
        semantic evaluator ONLY; keyword cues are diagnostics.
        """
        self._check_cancel()
        from odar.dialectic import COUNTER_QUERY_TEMPLATES, score_cues

        snippets: List[Dict[str, Any]] = []
        for snippet in existing_evidence or []:
            snippets.append({"snippet": snippet, "origin": "prior-evidence"})
        queries_used: List[str] = []
        for template in COUNTER_QUERY_TEMPLATES[: max(0, int(max_counter_queries))]:
            self._check_cancel()
            query = template.format(h=hypothesis)
            try:
                hits = self.search(query, max_results=4)  # one approval per call
            except BudgetExceeded as exc:
                logger.info("dialectic counter-search stopped by budget: %s", exc)
                break
            queries_used.append(query)
            for hit in hits[:4]:
                snippets.append({"snippet": hit.snippet, "origin": "counter-search", "url": hit.url})

        semantic_relations: List[Dict[str, Any]] = []
        cue_diagnostics: List[Dict[str, Any]] = []
        verdict = "UNDECIDED"
        if evaluator is not None:
            for item in snippets:
                text = item.get("snippet", "")
                if not text:
                    continue
                relation, confidence = evaluator.stance(hypothesis, text)
                semantic_relations.append(
                    {
                        "relation": relation.value,
                        "confidence": round(float(confidence), 4),
                        "origin": item.get("origin", ""),
                    }
                )
            if any(r["relation"] == Relation.REFUTES.value for r in semantic_relations):
                verdict = "REFUTED"
            elif any(r["relation"] == Relation.SUPPORTS.value for r in semantic_relations):
                verdict = "UPHELD"
        # Cues are recorded strictly as diagnostics - never decision input.
        for item in snippets:
            cues = score_cues(item.get("snippet", ""))
            cue_diagnostics.append(
                {
                    "support_cues": cues.get("support_score", 0),
                    "refute_cues": cues.get("refute_score", 0),
                    "origin": item.get("origin", ""),
                }
            )
        self.telemetry.count("dialectic_attacks")
        return DialecticOutcome(
            verdict=verdict,
            counter_queries=queries_used,
            semantic_relations=semantic_relations,
            cue_diagnostics=cue_diagnostics,
        )


def _new_id(prefix: str) -> str:
    from odar.schemas import new_id

    return new_id(prefix)
