"""The goal-driven research engine.

Architecture principles (enforced, not aspirational):

* The **model** proposes; the **Governor** disposes.  Every side-effecting
  capability executes through ONE boundary: :class:`odar.tools.GovernedExecutor`.
* A provisional/heuristic evidence evaluation can NEVER become a CERTIFIED
  claim (defense in depth across auditor, engine, state and final audit).
* LLM backend failures never silently change the reasoning architecture:
  the run fails, or falls back ONLY under an explicit policy, and the
  resulting artifact exposes the backend state.
* Claims are re-evaluated when materially new evidence arrives.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from odar.budget import Budget, BudgetExceeded, CancellationToken, CancelledError, Governor
from odar.citation_auditor import CitationAuditor, normalize_span, split_sentences
from odar.evidence import (
    CIRCULARITY_THRESHOLD,
    Claim,
    Relation,
    Uncertainty,
    detect_circularity,
    provenance_complete,
)
from odar.evidence_eval import SemanticStanceEvaluator
from odar.final_audit import AuditReport, FinalAuditor
from odar.llm import (
    ACTIONS,
    ModelBackendError,
    ModelDecision,
    NativeToolUseController,
    SYNTH_SYSTEM_PROMPT,
    LLMRefutationJudge,
    ScriptedResearchController,
    compact_query,
    synthesis_prompt,
    validate_synthesis,
)
from odar.research_state import ResearchState
from odar.retrieval import PageExtractor, ZeroCostSearch
from odar.sandbox import ExecutionSandbox
from odar.schemas import new_id
from odar.source_quality import CLASS_WEIGHTS
from odar.telemetry import TelemetryRecorder
from odar.tools import GovernedExecutor
from odar.trust import redact_secrets, sanitize_external_text

logger = logging.getLogger(__name__)

MAX_LOOP_ITERATIONS = 24
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Fallback policies for LLM backend failures.  "fail" (default) terminates
# the run explicitly; "explicit-scripted" continues under the scripted
# controller and marks every artifact DEGRADED.  There is no silent path.
FALLBACK_FAIL = "fail"
FALLBACK_EXPLICIT_SCRIPTED = "explicit-scripted"


def _md_link(label: str, url: str) -> str:
    """Clickable markdown link; brackets in titles are neutralised."""
    clean = " ".join((label or url).replace("[", "(").replace("]", ")").split())[:160]
    if not url:
        return clean
    return f"[{clean}]({url.replace(' ', '%20').replace(')', '%29')})"


def _looks_like_headline(sentence: str) -> bool:
    """Navigation items, headlines and bylines are not factual claims."""
    words = sentence.split()
    alpha = [w for w in words if w[:1].isalpha()]
    if not alpha:
        return True
    capitalised = sum(1 for w in alpha if w[:1].isupper())
    if len(alpha) >= 4 and capitalised / len(alpha) > 0.6:
        return True  # Title Case Headline
    head = sentence.split(":", 1)[0]
    if ":" in sentence and len(head.split()) <= 4 and not sentence.rstrip().endswith("."):
        return True  # "Interactive: Tracking ..." style section labels
    return sentence.rstrip().endswith("?")  # questions are not assertions


def _candidate_sentences(text: str, minimum: int = 40, maximum: int = 260) -> List[str]:
    """Grammatically complete declarative sentences only.

    Line breaks are hard boundaries (headlines and nav items never fuse into
    a "sentence"), trailing list enumerators are stripped, and headline /
    question / label fragments are excluded: mid-sentence truncations and
    page chrome can never be NLI-entailed meaningfully, so they are excluded
    by construction.
    """
    sentences: List[str] = []
    seen: set = set()
    for line in (text or "").splitlines():
        for part in split_sentences(line):
            part = normalize_span(part)
            if not (minimum <= len(part) <= maximum) or not part.endswith((".", "!")):
                continue
            if len(part.split()) < 8 or _looks_like_headline(part):
                continue
            if part.lower() in seen:
                continue
            seen.add(part.lower())
            sentences.append(part)
    return sentences


MAX_CLAIMS = 12
MAX_CLAIMS_PER_SOURCE = 3


@dataclass
class ResearchOutcome:
    state: ResearchState
    synthesis: str = ""
    audit: Optional[AuditReport] = None
    status: str = "RUNNING"
    backend_state: str = "unknown"  # always visible in artifacts
    degraded: bool = False
    governance: Dict[str, Any] = field(default_factory=dict)  # budget accounting snapshot
    events: List[Dict[str, Any]] = field(default_factory=list)
    telemetry: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "backend_state": self.backend_state,
            "degraded": self.degraded,
            "objective": self.state.objective,
            "run_id": self.state.run_id,
            "uncertainty": self.state.uncertainty.value,
            "termination_status": self.state.termination_status,
            "unmet_conditions": self.state.unmet_conditions,
            "digest": self.state.digest(),
            "synthesis": self.synthesis,
            "audit": self.audit.to_dict() if self.audit else None,
            "errors": self.errors,
            # Governance observability: budget accounting + events belong in
            # the artifact so ACTUAL vs ACCOUNTED can be audited afterwards.
            "governance": self.governance,
            "telemetry": self.telemetry,
            "events": self.events,
        }


class ResearchEngine:
    def __init__(
        self,
        model: Any,
        search: Optional[ZeroCostSearch] = None,
        extractor: Optional[PageExtractor] = None,
        auditor: Optional[CitationAuditor] = None,
        budget: Optional[Budget] = None,
        telemetry: Optional[TelemetryRecorder] = None,
        sandbox: Optional[ExecutionSandbox] = None,
        token: Optional[CancellationToken] = None,
        cancel_check: Optional[Any] = None,
        checkpoint_sink: Optional[Any] = None,
        fallback_policy: str = FALLBACK_FAIL,
    ) -> None:
        self.model = model
        self.search = search or ZeroCostSearch()
        self.extractor = extractor or PageExtractor()
        self.auditor = auditor or CitationAuditor()
        self.stance = SemanticStanceEvaluator(self.auditor)
        self.budget = budget or Budget()
        self.governor = Governor(self.budget, token=token)
        self.telemetry = telemetry or TelemetryRecorder()
        self.token = token or CancellationToken()
        self.final_auditor = FinalAuditor()
        self.cancel_check = cancel_check  # durable-job cancellation polling
        self.checkpoint_sink = checkpoint_sink  # callback(state) per action
        self.fallback_policy = fallback_policy
        self._security_violations: List[str] = []
        self._answer_cache: Dict[tuple, str] = {}
        if isinstance(model, NativeToolUseController):
            # Production path: NLI contradictions get a second opinion.
            self.auditor.refutation_judge = LLMRefutationJudge(model, self.governor)
        # THE single governed execution boundary.
        self.executor = GovernedExecutor(
            governor=self.governor,
            search=self.search,
            extractor=self.extractor,
            sandbox=sandbox or ExecutionSandbox(),
            auditor=self.auditor,
            retry_policy=None,
            telemetry=self.telemetry,
            token=self.token,
        )

    # ================================================================== #
    # Public entry point
    # ================================================================== #
    def run(self, question: str, resume_state: Optional[ResearchState] = None) -> ResearchOutcome:
        state = resume_state if resume_state is not None else ResearchState(objective=question.strip())
        if resume_state is not None:
            state.termination_status = "RUNNING"
            state.note("resumed from durable checkpoint")
            # A resumed run gets ONE additional governed probe: retrieval
            # conditions may have changed, and newly available evidence can
            # contradict even certified findings.
            state.resume_probe = True
        outcome = ResearchOutcome(state=state)
        outcome.backend_state = getattr(self.model, "backend", "unknown")
        run_id = state.run_id
        self.telemetry.event(
            "run.start",
            run_id,
            objective=question[:200],
            resumed=resume_state is not None,
            backend=outcome.backend_state,
        )
        try:
            self._dispatch_loop(state, outcome)
        except CancelledError:
            state.termination_status = "CANCELLED"
            self.telemetry.count("cancelled")
        except Exception as exc:  # never leak raw exceptions to callers
            state.termination_status = "FAILED"
            outcome.errors.append(redact_secrets(f"{type(exc).__name__}: {exc}"))
            self.telemetry.count("run_errors")
            logger.exception("engine run failed")

        unmet = self._termination_conditions(state, outcome)
        state.unmet_conditions = unmet
        if state.termination_status == "RUNNING":
            state.termination_status = "COMPLETE" if not unmet else "INCOMPLETE"
        outcome.status = state.termination_status
        outcome.events = self.telemetry.dump_events()
        outcome.telemetry = self.telemetry.summary()
        outcome.governance = dict(self.governor.report())
        self.telemetry.event(
            "run.end",
            run_id,
            status=outcome.status,
            unmet=len(unmet),
            backend=outcome.backend_state,
            degraded=outcome.degraded,
        )
        return outcome

    def _dispatch_loop(self, state: ResearchState, outcome: ResearchOutcome) -> None:
        """Run the reasoning loop for the configured controller (override
        point for alternative pipelines, e.g. ``odar.deep``)."""
        if isinstance(self.model, NativeToolUseController):
            self._run_tool_use_loop(state, outcome)
        else:
            self._run_scripted_loop(state, outcome)

    # ================================================================== #
    # Scripted-controller loop (offline backend)
    # ================================================================== #
    def _run_scripted_loop(self, state: ResearchState, outcome: ResearchOutcome) -> None:
        while state.termination_status == "RUNNING":
            self._loop_checks()
            try:
                self.governor.approve_iteration()
            except BudgetExceeded as exc:
                self._budget_exhausted(state, exc)
                break
            state.iteration += 1
            self.telemetry.count("iterations")

            decision = self._decide_scripted(state)
            self.telemetry.event("decision", state.run_id, **decision.to_dict())
            if decision.action not in ACTIONS:
                state.note(f"unknown action {decision.action!r} rejected by governor")
                self.telemetry.count("invalid_actions")
                continue

            terminal = self._execute(decision, state, outcome)
            self._checkpoint(state)
            if terminal:
                break
            if state.iteration >= MAX_LOOP_ITERATIONS:
                state.termination_status = "INCOMPLETE"
                state.note("hard iteration cap reached")
                break

    # ================================================================== #
    # Native tool-use loop (production LLM backend)
    # ================================================================== #
    def _run_tool_use_loop(self, state: ResearchState, outcome: ResearchOutcome) -> None:
        controller: NativeToolUseController = self.model
        while state.termination_status == "RUNNING":
            self._loop_checks()
            try:
                self.governor.approve_iteration()
            except BudgetExceeded as exc:
                self._budget_exhausted(state, exc)
                break
            state.iteration += 1
            self.telemetry.count("iterations")

            try:
                session = controller.run_tool_session(state, self.executor)
            except ModelBackendError as exc:
                self._handle_backend_failure(exc, state, outcome)
                if state.termination_status != "RUNNING":
                    break
                # explicit fallback enabled: switch reasoning architecture
                # VISIBLY and continue under the scripted controller.
                self.model = ScriptedResearchController()
                outcome.backend_state = f"scripted-explicit-fallback (after {exc.kind})"
                outcome.degraded = True
                state.note(
                    f"LLM backend failed ({exc.kind}); explicit policy fallback to scripted controller"
                )
                self._run_scripted_loop(state, outcome)
                return
            except BudgetExceeded as exc:
                self._budget_exhausted(state, exc)
                break

            self.telemetry.event("tool_session", state.run_id, **session.to_dict())
            for call in session.tool_calls:
                state.record_action(f"tool:{call['tool']}")

            # Engine-owned post-processing: extraction, evaluation,
            # contradiction examination.  The model never certifies anything.
            progressed = False
            if any(sid not in state.mined_source_ids for sid in state.sources):
                self._action_extract_claims(state)
                progressed = True
            pending_eval = [
                c.claim_id
                for c in state.claims.values()
                if c.needs_evaluation or not state.evidence_for_claim(c.claim_id)
            ]
            if pending_eval and state.sources:
                self._action_evaluate({"claim_ids": pending_eval[:6]}, state)
                progressed = True
            open_conflicts = [c for c in state.contradictions if not c.get("examined")]
            if open_conflicts:
                self._action_dialectic({"claim_id": open_conflicts[0].get("claim_id", "")}, state)
                progressed = True

            if session.finish_requested or (not session.tool_calls and not progressed):
                self._finalize(state, outcome)
                self._checkpoint(state)
                break
            self._checkpoint(state)
            if state.iteration >= MAX_LOOP_ITERATIONS:
                state.termination_status = "INCOMPLETE"
                state.note("hard iteration cap reached")
                break

    # ------------------------------------------------------------------ #
    def _loop_checks(self) -> None:
        self.token.check()
        if self.cancel_check is not None:
            try:
                if self.cancel_check():
                    raise CancelledError("cancel requested via job store")
            except CancelledError:
                raise
            except Exception:
                pass

    def _budget_exhausted(self, state: ResearchState, exc: BudgetExceeded) -> None:
        state.termination_status = "BUDGET_EXHAUSTED"
        state.note(f"budget exhausted: {exc}")
        self.telemetry.count("budget_exhausted")

    def _checkpoint(self, state: ResearchState) -> None:
        if self.checkpoint_sink is not None:
            try:
                self.checkpoint_sink(state)
            except Exception as exc:  # checkpointing must never break the run
                logger.warning("checkpoint sink failed: %s", exc)

    def _handle_backend_failure(
        self, exc: ModelBackendError, state: ResearchState, outcome: ResearchOutcome
    ) -> None:
        self.telemetry.count("backend_failures")
        self.telemetry.count(f"backend_failure_{exc.kind}")
        state.note(f"model backend failure: {exc.kind}: {str(exc)[:120]}")
        if self.fallback_policy == FALLBACK_EXPLICIT_SCRIPTED:
            return  # caller switches backends explicitly (visible)
        # Default policy: explicit failure.  No silent architecture switch.
        state.termination_status = "FAILED"
        outcome.errors.append(f"model backend failure ({exc.kind}): {str(exc)[:160]}")
        outcome.degraded = False

    # ================================================================== #
    def _decide_scripted(self, state: ResearchState) -> ModelDecision:
        started = time.perf_counter()
        try:
            decision = self.model.decide(state, self.governor.report())
        except Exception as exc:
            self.telemetry.count("model_errors")
            state.note(f"model error: {type(exc).__name__}")
            decision = ScriptedResearchController().decide(state, self.governor.report())
            decision.backend = "scripted-error-recovery"
        self.telemetry.count("model_calls")
        self.telemetry.latency("model.decide", time.perf_counter() - started)
        return decision

    # ================================================================== #
    # Action execution - ALL side effects route through the executor
    # ================================================================== #
    def _execute(self, decision: ModelDecision, state: ResearchState, outcome: ResearchOutcome) -> bool:
        action = decision.action
        params = decision.params or {}
        if action == "search":
            self._action_search(params, state)
        elif action == "fetch":
            self._action_fetch(params, state)
        elif action == "extract_claims":
            self._action_extract_claims(state)
        elif action == "evaluate_evidence":
            self._action_evaluate(params, state)
        elif action == "dialectic_attack":
            self._action_dialectic(params, state)
        elif action == "synthesize":
            self._action_synthesize(state, outcome)
        elif action == "request_extension":
            granted = self.governor.request_extension(str(params.get("justification", "")))
            state.note(f"extension {'granted' if granted else 'denied'}: {params.get('justification', '')}")
            self.telemetry.count("extension_requests")
        elif action == "finish":
            self._action_finish(params, state, outcome)
            return True
        return False

    # ------------------------------------------------------------------ #
    def _action_search(self, params: Dict[str, Any], state: ResearchState) -> None:
        query = str(params.get("query", "")).strip() or compact_query(state.objective)
        try:
            hits = self.executor.search(query, max_results=6)
        except BudgetExceeded as exc:
            state.note(f"search denied: {exc}")
            return
        state.record_query(query)
        state.record_action(f"search:{query[:80]}")
        if not hits:
            state.note(f"no hits for {query[:60]!r}")
            self.telemetry.count("search_empty")
        else:
            self.telemetry.count("search_hits", len(hits))
        if isinstance(self.model, ScriptedResearchController):
            self.model.offer_hits(hits)

    # ------------------------------------------------------------------ #
    def _action_fetch(self, params: Dict[str, Any], state: ResearchState) -> None:
        url = str(params.get("url", "")).strip()
        if not url:
            return
        try:
            outcome = self.executor.fetch(url, state.objective, state.fetched_urls)
        except BudgetExceeded as exc:
            state.note(f"fetch denied: {exc}")
            return
        state.record_action(f"fetch:{url[:80]}")
        if outcome.quarantined:
            state.quarantined_urls.append(url)
            state.injection_blocked += 1
            state.note(f"page quarantined (injection signatures): {url[:80]}")
            return
        if outcome.denied:
            state.note(f"fetch denied: {outcome.denied}")
            return
        if outcome.source is None:
            state.failed_approaches.append(f"fetch:{url}")
            state.note(f"fetch failed or thin: {(outcome.error or 'thin')[:80]}")
            return
        state.add_source(outcome.source)
        state.mark_new_source(outcome.source.source_id)  # triggers re-evaluation
        self.telemetry.count("sources_added")

    # ------------------------------------------------------------------ #
    def _action_extract_claims(self, state: ResearchState) -> None:
        """Mine candidate claims from sources not mined yet.

        Incremental: every newly fetched source is mined once (so later
        sources that cover a different facet of the question - e.g. VACUUM
        after isolation levels - contribute claims), at most
        ``MAX_CLAIMS_PER_SOURCE`` per source and ``MAX_CLAIMS`` per run,
        best-quality sources first, most on-topic sentences first.
        """
        if not state.sources:
            state.note("extract_claims skipped: no sources")
            return
        unmined = [s for s in state.sources.values() if s.source_id not in state.mined_source_ids]
        best = sorted(
            unmined,
            key=lambda s: (CLASS_WEIGHTS.get(s.publisher_class, 0.3), len(s.extracted_text)),
            reverse=True,
        )
        objective_tokens = set(compact_query(state.objective, max_terms=24).split())
        added = 0
        for source in best:
            state.mined_source_ids.append(source.source_id)
            if len(state.claims) >= MAX_CLAIMS:
                continue
            scored = []
            for position, sentence in enumerate(_candidate_sentences(source.extracted_text)):
                tokens = {token.lower().strip(".,;:()[]\"'") for token in sentence.split()}
                hits = len(tokens & objective_tokens)
                if hits >= 2:
                    scored.append((-hits, position, sentence))
            scored.sort()
            taken = 0
            for _neg, _pos, sentence in scored:
                if taken >= MAX_CLAIMS_PER_SOURCE or len(state.claims) >= MAX_CLAIMS:
                    break
                if any(
                    detect_circularity(candidate.text, sentence) >= CIRCULARITY_THRESHOLD
                    for candidate in state.claims.values()
                ):
                    continue
                claim = Claim(claim_id=new_id("clm"), text=sentence[:400], uncertainty=Uncertainty.UNCERTAIN)
                state.add_claim(claim)
                taken += 1
                added += 1
        state.record_action(f"extract_claims:{len(state.claims)}")
        self.telemetry.count("claims_extracted", added)

    # ------------------------------------------------------------------ #
    def _action_evaluate(self, params: Dict[str, Any], state: ResearchState) -> None:
        target_ids = params.get("claim_ids") or list(state.claims.keys())
        sources = list(state.sources.values())
        texts = [s.extracted_text for s in sources]
        source_ids = [s.source_id for s in sources]
        for claim_id in target_ids:
            claim = state.claims.get(claim_id)
            if claim is None:
                continue
            # Re-evaluation rule: a claim is evaluated when it has never been
            # evaluated OR when new sources arrived since the last evaluation.
            new_since_eval = [sid for sid in source_ids if sid not in claim.evaluated_sources]
            if not new_since_eval and not claim.needs_evaluation and state.evidence_for_claim(claim_id):
                continue
            selector = params.get("source_selector")
            if selector is not None:
                # Multi-agent mode: verify against the claim's own
                # sub-question sources plus the most related other sources.
                chosen = list(selector(claim)) or sources
                chosen_texts = [s.extracted_text for s in chosen]
            else:
                chosen, chosen_texts = sources, texts
            try:
                result, items, gaps = self.executor.evaluate_claim(claim, chosen, chosen_texts)
            except BudgetExceeded:
                state.note("evidence evaluation denied (model budget)")
                break
            self.telemetry.count("evidence_evaluations")
            for item in items:
                state.add_evidence(item)
            claim.evaluated_sources = list(source_ids)
            claim.needs_evaluation = False
            if gaps:
                state.note(f"provenance gaps for {claim_id}: {gaps[0]}")
            supports = [i for i in items if i.relation is Relation.SUPPORTS]
            refutes = [i for i in items if i.relation is Relation.REFUTES]
            if supports and refutes:
                # Genuinely conflicting evidence: never certify, never hide.
                claim.status = "CONFLICTING"
                claim.provisional = False
                claim.uncertainty = Uncertainty.CONFLICTING_EVIDENCE
                state.contradictions.append(
                    {
                        "claim_id": claim_id,
                        "examined": False,
                        "note": (
                            f"support p={max(i.entailment_probability for i in supports):.2f} vs "
                            f"refutation p={max(i.contradiction_probability for i in refutes):.2f}"
                        ),
                    }
                )
                self.telemetry.count("contradictions_recorded")
                continue
            if result.certified and supports and not result.provisional:
                # CERTIFIED requires a real (non-provisional) verifier.
                claim.status = "CERTIFIED"
                claim.provisional = False
                claim.uncertainty = Uncertainty.EVIDENCE_FOR
                self.telemetry.count("claims_certified")
            elif result.provisional and supports and result.verdict.value == "ENTAILMENT":
                # Heuristic verifier: diagnostics only.  NEVER certified.
                claim.status = "PROVISIONAL"
                claim.provisional = True
                claim.uncertainty = Uncertainty.LOW_QUALITY_EVIDENCE
                self.telemetry.count("claims_provisional")
            elif refutes and refutes[0].contradiction_probability > max(
                (i.entailment_probability for i in supports), default=0.0
            ):
                claim.status = "REFUTED"
                claim.provisional = False
                claim.uncertainty = Uncertainty.CONFLICTING_EVIDENCE
                state.contradictions.append(
                    {
                        "claim_id": claim_id,
                        "examined": False,
                        "note": f"contradiction probability {refutes[0].contradiction_probability:.2f}",
                    }
                )
                self.telemetry.count("contradictions_recorded")
            elif supports:
                claim.status = "SUPPORTED"
                claim.uncertainty = (
                    Uncertainty.EVIDENCE_FOR if not result.provisional else Uncertainty.LOW_QUALITY_EVIDENCE
                )
                claim.provisional = result.provisional
            else:
                claim.status = "INSUFFICIENT"
                claim.uncertainty = Uncertainty.INSUFFICIENT_EVIDENCE
        state.record_action(f"evaluate_evidence:{len(target_ids)}")

    # ------------------------------------------------------------------ #
    def _action_dialectic(self, params: Dict[str, Any], state: ResearchState) -> None:
        claim_id = str(params.get("claim_id", ""))
        conflict = next((c for c in state.contradictions if c.get("claim_id") == claim_id), None)
        claim = state.claims.get(claim_id)
        if claim is None:
            if conflict is not None:
                conflict["examined"] = True
            return
        # Every counter-query is budgeted inside executor.dialectic
        # (one governor approval per actual network search).
        result = self.executor.dialectic(claim.text, evaluator=self.stance, max_counter_queries=1)
        if result.verdict == "REFUTED":
            claim.status = "REFUTED"
            claim.uncertainty = Uncertainty.CONFLICTING_EVIDENCE
            self.telemetry.count("claims_refuted")
        state.note(
            f"dialectic {result.verdict} for {claim_id} "
            f"(semantic relations: {[r['relation'] for r in result.semantic_relations] or 'none'}; "
            f"cues are diagnostics only)"
        )
        if conflict is not None:
            conflict["examined"] = True
            conflict["stance"] = result.verdict

    # ------------------------------------------------------------------ #
    def _finalize(self, state: ResearchState, outcome: ResearchOutcome) -> None:
        if state.supporting_claims() or state.provisional_claims():
            self._action_synthesize(state, outcome)
        else:
            self._action_finish({"uncertainty": state.uncertainty.value}, state, outcome)

    # ------------------------------------------------------------------ #
    def _build_synthesis(self, state: ResearchState, outcome: ResearchOutcome) -> str:
        """Pure builder: the report is generated strictly from the evidence
        graph - no claim that is absent from the research state may appear."""
        certified = state.supporting_claims()
        provisional = state.provisional_claims()
        lines = [f"# Research Report: {state.objective}", ""]
        if outcome.degraded or outcome.backend_state.startswith("scripted-explicit-fallback"):
            lines.append(
                "> **DEGRADED**: the configured LLM backend failed; this run continued under an "
                "explicitly configured fallback controller. Treat findings accordingly."
            )
            lines.append("")
        source_numbers: Dict[str, int] = {}
        if certified:
            claim_sources = self._claim_sources(state, certified)
            for claim in certified:
                for source_id in claim_sources[claim.claim_id]:
                    source_numbers.setdefault(source_id, len(source_numbers) + 1)
            answer = self._answer_text(state, certified, claim_sources, source_numbers)
            if answer:
                lines.append("## Answer (written only from the certified findings below)")
                lines.append(answer)
                lines.append("")
            lines.append("## Certified Findings (semantic NLI verification)")
            for claim in certified:
                evidence = state.evidence_for_claim(claim.claim_id)
                supports = [e for e in evidence if e.relation is Relation.SUPPORTS]
                lines.append(f"- [{claim.claim_id}] {claim.text}")
                for e in supports[:3]:
                    source = state.sources.get(e.source_id)
                    if source is not None:
                        lines.append(
                            f"  - cited: [{source.source_id}] {_md_link(source.title or source.url, source.url)} "
                            f"(entailment p={e.entailment_probability:.2f}, evaluator: {e.evaluated_by})"
                        )
        if provisional:
            lines.append("")
            lines.append("## Provisional Findings (HEURISTIC verification only - NOT certified)")
            for claim in provisional:
                lines.append(
                    f"- [{claim.claim_id}] {claim.text} *(provisional: neural verifier unavailable)*"
                )
        refuted = [c for c in state.claims.values() if c.status in ("CONTRADICTED", "REFUTED", "CONFLICTING")]
        if refuted:
            lines.append("")
            lines.append("## Contradicted / Refuted / Conflicting")
            for claim in refuted:
                lines.append(f"- [{claim.claim_id}] {claim.text} *(status: {claim.status})*")
        if state.contradictions:
            lines.append("")
            lines.append("## Contradictions Examined")
            for conflict in state.contradictions:
                lines.append(
                    f"- {conflict.get('claim_id')}: examined={conflict.get('examined')} "
                    f"stance={conflict.get('stance', 'n/a')}"
                )
        lines.append("")
        lines.append("## Uncertainty and Limitations")
        insufficient = [c for c in state.claims.values() if c.status in ("UNCERTAIN", "INSUFFICIENT")]
        if insufficient:
            lines.append(f"- {len(insufficient)} claim(s) remain uncertain or insufficiently evidenced.")
        if not any(s.publisher_class == "primary_research" for s in state.sources.values()):
            lines.append(
                "- No primary-research source was available; findings rest on secondary reporting "
                "and are limited accordingly."
            )
        if self.auditor.scorer_backend != self.auditor.model_name:
            lines.append(
                "- Verification ran without the neural NLI model; findings above are heuristic and "
                "no claim could be certified."
            )
        if state.injection_blocked:
            lines.append(
                f"- {state.injection_blocked} page(s) were quarantined for suspected prompt injection "
                "and excluded."
            )
        lines.append("- Uncertainty classification: " + state.uncertainty.value.replace("_", " "))
        if source_numbers:
            lines.append("")
            lines.append("## Sources")
            for source_id, number in sorted(source_numbers.items(), key=lambda item: item[1]):
                source = state.sources[source_id]
                lines.append(f"{number}. [{source_id}] {_md_link(source.title or source.url, source.url)}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _claim_sources(state: ResearchState, certified: List[Claim]) -> Dict[str, List[str]]:
        """claim_id -> ids of sources with a SUPPORTS span (strongest first)."""
        mapping: Dict[str, List[str]] = {}
        for claim in certified:
            supports = sorted(
                (e for e in state.evidence_for_claim(claim.claim_id) if e.relation is Relation.SUPPORTS),
                key=lambda e: e.entailment_probability,
                reverse=True,
            )
            ids: List[str] = []
            for e in supports:
                if e.source_id in state.sources and e.source_id not in ids:
                    ids.append(e.source_id)
            mapping[claim.claim_id] = ids[:3]
        return mapping

    def _answer_text(
        self,
        state: ResearchState,
        certified: List[Claim],
        claim_sources: Dict[str, List[str]],
        source_numbers: Dict[str, int],
    ) -> str:
        """Readable answer composed ONLY from certified claims.

        LLM path: the model rewrites the certified facts into prose; every
        sentence must cite fact numbers, and uncited / unresolvable sentences
        are dropped (``validate_synthesis``).  Offline path or model failure:
        a deterministic composition of the certified sentences.  Fact markers
        are rewritten into clickable source links.
        """
        key = tuple(c.claim_id for c in certified)
        if key in self._answer_cache:
            return self._answer_cache[key]
        facts = [c.text for c in certified]
        prose = ""
        if isinstance(self.model, NativeToolUseController):
            try:
                raw = self.model.complete_text(
                    synthesis_prompt(state.objective, facts),
                    SYNTH_SYSTEM_PROMPT,
                    self.governor,
                    max_tokens=900,
                )
                prose = validate_synthesis(
                    sanitize_external_text(raw, max_chars=6000),
                    len(facts),
                    facts=facts,
                    objective=state.objective,
                )
                self.telemetry.count("answer_synthesized_llm")
            except (BudgetExceeded, ModelBackendError) as exc:
                state.note(
                    f"answer synthesis unavailable ({type(exc).__name__}); deterministic composition used"
                )
                prose = ""
        if not prose:
            prose = " ".join(f"{fact.rstrip()} [{i}]" for i, fact in enumerate(facts, start=1))

        def _link(match: "re.Match[str]") -> str:
            index = int(match.group(1)) - 1
            if not 0 <= index < len(certified):
                return ""
            refs = []
            for source_id in claim_sources.get(certified[index].claim_id, [])[:2]:
                source = state.sources[source_id]
                refs.append(f"[[{source_numbers[source_id]}]]({source.url})")
            return "".join(refs)

        answer = re.sub(r"\[(\d{1,3})\]", _link, prose)
        # Collapse repeated identical citations ("[[4]](u)[[4]](u)" -> once).
        answer = re.sub(r"(\[\[\d+\]\]\([^)\s]*\))(?:\s*\1)+", r"\1", answer)
        self._answer_cache[key] = answer
        return answer

    def _action_synthesize(self, state: ResearchState, outcome: ResearchOutcome) -> None:
        if not state.supporting_claims() and not state.provisional_claims():
            state.note("synthesize requested without supported claims; abstaining")
            self._action_finish({"uncertainty": state.uncertainty.value}, state, outcome)
            return

        synthesis = self._build_synthesis(state, outcome)
        report = self.final_auditor.audit(state, synthesis)
        self.telemetry.count("final_audits")
        if report.status == "FAIL":
            # Demote implicated claims ONCE and rebuild (no recursion).
            self.telemetry.count("final_audit_failures")
            state.note(f"final audit FAILED: {report.findings[:2]}")
            implicated = (
                set(report.circular_citations)
                | set(report.unsupported_claims)
                | set(report.provisional_certifications)
            )
            for claim_id in implicated:
                claim = state.claims.get(claim_id)
                if claim is not None:
                    claim.status = "PROVISIONAL" if claim.provisional else "INSUFFICIENT"
                    claim.uncertainty = Uncertainty.INSUFFICIENT_EVIDENCE
            if not state.supporting_claims() and not state.provisional_claims():
                outcome.audit = report
                state.uncertainty = Uncertainty.INSUFFICIENT_EVIDENCE
                self._action_finish({"uncertainty": state.uncertainty.value}, state, outcome)
                return
            synthesis = self._build_synthesis(state, outcome)
            report = self.final_auditor.audit(state, synthesis)
        outcome.audit = report
        outcome.synthesis = synthesis
        state.uncertainty = self._classify_uncertainty(state)
        state.termination_status = "COMPLETE"

    def _action_finish(self, params: Dict[str, Any], state: ResearchState, outcome: ResearchOutcome) -> None:
        requested = str(params.get("uncertainty", "")).strip().lower()
        mapping = {
            "no_evidence_found": Uncertainty.NO_EVIDENCE_FOUND,
            "insufficient_evidence": Uncertainty.INSUFFICIENT_EVIDENCE,
            "contradicted": Uncertainty.CONFLICTING_EVIDENCE,
            "certain": Uncertainty.EVIDENCE_FOR,
        }
        state.uncertainty = mapping.get(requested, self._classify_uncertainty(state))
        if not outcome.synthesis:
            verifier_note = ""
            if self.auditor.scorer_backend != self.auditor.model_name:
                verifier_note = (
                    "\nVerification note: neural NLI model unavailable; only heuristic verification "
                    "was possible, so nothing could be certified.\n"
                )
            outcome.synthesis = (
                f"# Research Report: {state.objective}\n\n"
                f"No certified findings could be established.\n"
                f"{verifier_note}\n"
                f"Uncertainty classification: {state.uncertainty.value.replace('_', ' ')}.\n"
                f"This report abstains rather than asserting unsupported conclusions."
            )
            outcome.audit = self.final_auditor.audit(state, outcome.synthesis)
        if state.termination_status == "RUNNING":
            state.termination_status = "COMPLETE"

    # ------------------------------------------------------------------ #
    def _classify_uncertainty(self, state: ResearchState) -> Uncertainty:
        if any(c.status in ("CONTRADICTED", "REFUTED", "CONFLICTING") for c in state.claims.values()):
            return Uncertainty.CONFLICTING_EVIDENCE
        if state.supporting_claims():
            if any(c.status in ("UNCERTAIN", "INSUFFICIENT") for c in state.claims.values()):
                return Uncertainty.UNCERTAIN
            return Uncertainty.EVIDENCE_FOR
        if state.provisional_claims():
            return Uncertainty.LOW_QUALITY_EVIDENCE
        if state.sources:
            return Uncertainty.INSUFFICIENT_EVIDENCE
        return Uncertainty.NO_EVIDENCE_FOUND

    # ================================================================== #
    # Termination condition matrix (all must hold for COMPLETE)
    # ================================================================== #
    def _termination_conditions(self, state: ResearchState, outcome: ResearchOutcome) -> List[str]:
        unmet: List[str] = []
        if not state.claims and not outcome.synthesis:
            unmet.append("objective_not_addressed")
        coverage = state.evidence_coverage()
        if any(ok is False for ok in coverage.values()) and not state.supporting_claims():
            unmet.append("claims_lack_semantic_evidence")
        if any(c for c in state.contradictions if not c.get("examined")):
            unmet.append("contradictions_unexamined")
        for claim in state.supporting_claims():
            for e in state.evidence_for_claim(claim.claim_id):
                source = state.sources.get(e.source_id)
                if source is None or provenance_complete(source, e):
                    unmet.append("provenance_incomplete")
                    break
            if "provenance_incomplete" in unmet:
                break
        if self._security_violations or state.termination_status == "SECURITY_HALT":
            unmet.append("security_policy_violated")
        if outcome.audit is not None and outcome.audit.status == "FAIL":
            unmet.append("final_audit_failed")
        if state.uncertainty is None:
            unmet.append("uncertainty_not_explicit")
        return unmet
