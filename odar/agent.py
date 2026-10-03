"""Unified Anthropic tool-use loop coordinator (ODAR v2.0).

Dual-mode model backends:

* :class:`LocalPolicyModel` - deterministic zero-cost policy emitting
  Anthropic-shaped ``tool_use`` events.  The $0 default.
* :class:`AnthropicSDKAdapter` - native official SDK integration
  (``anthropic.AsyncAnthropic``) with **prompt caching**: the system prompt
  and the tool-definition array carry ``cache_control: {"type":
  "ephemeral"}`` so the prefix KV cache is preserved across loop turns,
  slashing latency and token cost.

Exact Messages tool loop lifecycle:

    request with tools -> stop_reason == "tool_use" -> dispatch tool ->
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": id,
    "content": text}]} -> re-invoke -> repeat until stop_reason ==
    "end_turn".

The loop itself is async-native (it drives the concurrent DAG scheduler);
:meth:`AgentLoop.run` remains as a synchronous facade.

Hard in-flight circuit breakers (poka-yoke):

* exactly **10** tool iterations per research node;
* Jaccard hard-dedup at **0.85** over full session history (dispatcher);
* semantic loop-breaking at **0.82** with orthogonal facet rotation;
* monotonic evidence-novelty quality check - the node stops with
  ``quality_plateau`` after **2** consecutive non-improving turns.

Tool exceptions never escape: they are formatted into structured JSON error
envelopes inside ``tool_result`` so the agent can autonomously diagnose and
recover.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from odar.citation_auditor import CitationAuditor
from odar.dialectic import DialecticalEngine
from odar.loop_breaker import (
    STOPWORDS,
    SemanticLoopBreaker,
    jaccard_similarity,
    tokenize,
)
from odar.retrieval import PageExtractor, ZeroCostSearch
from odar.sandbox import ExecutionSandbox
from odar.schemas import (
    AgentTrace,
    ModelEvent,
    StopReason,
    ToolSpec,
    new_id,
    utc_timestamp,
    validate_payload,
)

logger = logging.getLogger("odar.agent")

JACCARD_DEDUP_THRESHOLD = 0.85
MAX_ITERATIONS_DEFAULT = 10
QUALITY_PLATEAU_PATIENCE = 2
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"
SYSTEM_PROMPT = (
    "You are ODAR, a rigorous research agent. Use tools to gather evidence, "
    "execute numeric work inside the hardened sandbox, falsify your own "
    "hypotheses, and only certify claims that pass NLI citation auditing. "
    "Prefer compact JSON digests over prose."
)

# --------------------------------------------------------------------------- #
# Tool registry: explicit JSON schemas with strict types
# --------------------------------------------------------------------------- #
TOOL_SPECS: List[ToolSpec] = [
    ToolSpec(
        name="web_search",
        description="Zero-cost cascading web search (DuckDuckGo + Wikipedia). Returns titles, URLs and snippets.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 3},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="extract_page",
        description="Fetch a URL and distil it to capped clean text via trafilatura.",
        input_schema={
            "type": "object",
            "properties": {"url": {"type": "string", "minLength": 8}},
            "required": ["url"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="run_python",
        description=(
            "Run Python in a kernel-hardened sandbox (AST policy gate, POSIX "
            "rlimits, wall-clock timeout). Use for all arithmetic, aggregation "
            "and table filtering."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "minLength": 1},
                "timeout": {"type": "number", "minimum": 1, "maximum": 60},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="dialectic_probe",
        description="Attack a hypothesis with targeted counter-queries and score the outcome.",
        input_schema={
            "type": "object",
            "properties": {
                "hypothesis": {"type": "string", "minLength": 5},
                "max_counter_queries": {"type": "integer", "minimum": 0, "maximum": 4},
            },
            "required": ["hypothesis"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="audit_claim",
        description=(
            "Certify a claim against source snippets with a local NLI cross-encoder "
            "(entailment threshold 0.75 across 4-8 spans)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "claim": {"type": "string", "minLength": 10},
                "sources": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            },
            "required": ["claim", "sources"],
            "additionalProperties": False,
        },
    ),
]

_SPEC_BY_NAME: Dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


class ToolDispatcher:
    """Executes registered tools with schema gates and poka-yoke error payloads."""

    def __init__(
        self,
        search: Optional[ZeroCostSearch] = None,
        extractor: Optional[PageExtractor] = None,
        sandbox: Optional[ExecutionSandbox] = None,
        dialectic: Optional[DialecticalEngine] = None,
        loop_breaker: Optional[SemanticLoopBreaker] = None,
        auditor_factory: Optional[Callable[[], CitationAuditor]] = None,
    ) -> None:
        self.search = search or ZeroCostSearch()
        self.extractor = extractor or PageExtractor()
        self.sandbox = sandbox or ExecutionSandbox()
        self.loop_breaker = loop_breaker or SemanticLoopBreaker()
        self.dialectic = dialectic or DialecticalEngine(search_fn=self.search.text)
        self.auditor_factory = auditor_factory or (lambda: CitationAuditor())
        self._auditor: Optional[CitationAuditor] = None
        self._query_memory: List[str] = []
        self.handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {
            "web_search": self._tool_web_search,
            "extract_page": self._tool_extract_page,
            "run_python": self._tool_run_python,
            "dialectic_probe": self._tool_dialectic_probe,
            "audit_claim": self._tool_audit_claim,
        }
        self.stats: Dict[str, int] = {
            "tool_calls": 0,
            "tool_errors": 0,
            "schema_rejections": 0,
            "dedup_rejections": 0,
            "degraded_results": 0,
        }

    # ------------------------------------------------------------------ #
    def dispatch(self, name: str, payload: Any) -> Tuple[str, bool]:
        """Run one tool call. Returns ``(result_text, is_error)``; never raises."""
        self.stats["tool_calls"] += 1
        spec = _SPEC_BY_NAME.get(name)
        if spec is None:
            self.stats["tool_errors"] += 1
            return json_dumps(
                {
                    "error": {
                        "type": "UnknownTool",
                        "message": f"tool '{name}' is not registered",
                        "available_tools": sorted(_SPEC_BY_NAME),
                        "recovery_hint": "Pick one of the available tools and retry.",
                    }
                }
            ), True

        violations = validate_payload(spec.input_schema, payload if payload is not None else {})
        if violations:
            self.stats["schema_rejections"] += 1
            return json_dumps(
                {
                    "error": {
                        "type": "SchemaViolation",
                        "message": "tool input failed schema validation",
                        "violations": violations,
                        "schema": spec.input_schema,
                        "recovery_hint": "Fix the listed violations and re-issue the tool call.",
                    }
                }
            ), True

        try:
            result = self.handlers[name](payload)
            # Envelope-level failures (ok=false digests from sandbox,
            # extraction or blocked searches) are surfaced as tool errors so
            # the loop can react, while structured payloads stay intact.
            is_error = isinstance(result, dict) and result.get("ok") is False
            if is_error:
                self.stats["degraded_results"] += 1
            return json_dumps(result), is_error
        except Exception as exc:  # poka-yoke: informative error payload, no crash
            self.stats["tool_errors"] += 1
            logger.warning("tool %s raised %s: %s", name, type(exc).__name__, exc)
            return json_dumps(
                {
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc)[:500],
                        "tool": name,
                        "recovery_hint": (
                            "The tool failed at runtime. Reformulate the input "
                            "(e.g. different query, different URL, simpler code) "
                            "or proceed with the evidence already collected."
                        ),
                    }
                }
            ), True

    # ------------------------------------------------------------------ #
    # Tool implementations
    # ------------------------------------------------------------------ #
    def _tool_web_search(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        query = payload["query"]
        max_results = int(payload.get("max_results", 6))
        for previous in self._query_memory:
            if jaccard_similarity(query, previous) >= JACCARD_DEDUP_THRESHOLD:
                self.stats["dedup_rejections"] += 1
                return {
                    "ok": False,
                    "blocked": "jaccard-dedup",
                    "reason": f"query is a near-duplicate (jaccard >= {JACCARD_DEDUP_THRESHOLD}) of an earlier query",
                    "previous_query": previous,
                    "results": [],
                    "recovery_hint": "Search a different facet instead of repeating this query.",
                }
        decision = self.loop_breaker.guard(query)
        if decision.action == "block":
            self.stats["dedup_rejections"] += 1
            return {
                "ok": False,
                "blocked": "loop-breaker",
                "reason": decision.reason,
                "results": [],
                "recovery_hint": "Issue a structurally different query.",
            }
        effective_query = decision.query
        hits = self.search.text(effective_query, max_results=max_results)
        self._query_memory.append(effective_query)
        return {
            "ok": True,
            "requested_query": query,
            "executed_query": effective_query,
            "loop_breaker": decision.to_dict(),
            "results": [hit.to_dict() for hit in hits],
        }

    def _tool_extract_page(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        page = self.extractor.extract(payload["url"])
        return page.to_dict()

    def _tool_run_python(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        timeout = payload.get("timeout")
        return self.sandbox.run_python(payload["code"], timeout=timeout)

    def _tool_dialectic_probe(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        report = self.dialectic.attack(
            payload["hypothesis"],
            max_counter_queries=int(payload.get("max_counter_queries", 2)),
        )
        return report.to_dict()

    def _tool_audit_claim(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._auditor is None:
            self._auditor = self.auditor_factory()
        result = self._auditor.audit(payload["claim"], payload["sources"])
        return result.to_dict()


# --------------------------------------------------------------------------- #
# Native Anthropic request builder (prompt caching)
# --------------------------------------------------------------------------- #
def build_anthropic_request(
    messages: List[Dict[str, Any]],
    tools: List[ToolSpec],
    model: str = DEFAULT_ANTHROPIC_MODEL,
    max_tokens: int = 1024,
    system_prompt: str = SYSTEM_PROMPT,
) -> Dict[str, Any]:
    """Build a Messages API payload with ephemeral prompt caching.

    ``cache_control`` is attached to the system prompt block and to the final
    tool definition, which anchors the cached prefix (system + tools) ahead
    of the mutable message tail - preserving the KV cache across every turn
    of the tool loop.
    """
    # Accept both ToolSpec objects and plain dict definitions (the native
    # tool-use controller uses canonical dict schemas).
    tool_defs: List[Dict[str, Any]] = []
    for spec in tools:
        if isinstance(spec, dict):
            tool_defs.append(dict(spec))
        else:
            tool_defs.append(spec.to_dict())
    if tool_defs:
        tool_defs[-1] = dict(tool_defs[-1])
        tool_defs[-1]["cache_control"] = {"type": "ephemeral"}
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": messages,
        "tools": tool_defs,
    }


class AnthropicSDKAdapter:
    """Native official-SDK model adapter (``anthropic.AsyncAnthropic``)."""

    def __init__(
        self,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        api_key: Optional[str] = None,
        max_tokens: int = 1024,
        system_prompt: str = SYSTEM_PROMPT,
        base_url: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:  # poka-yoke: SDK absence degrades cleanly
            raise RuntimeError(
                "the 'anthropic' SDK is not installed; run `pip install anthropic` "
                "or use the zero-cost LocalPolicyModel"
            ) from exc
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not resolved_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set; the zero-cost LocalPolicyModel "
                "is used instead of the paid SDK adapter"
            )
        self.model = model
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        client_kwargs: Dict[str, Any] = {"api_key": resolved_key}
        if base_url:  # allows pointing at a verified local/mock endpoint
            client_kwargs["base_url"] = base_url
        if request_timeout:
            client_kwargs["timeout"] = float(request_timeout)
        self._client = anthropic.AsyncAnthropic(**client_kwargs)

    async def next_response(
        self,
        messages: List[Dict[str, Any]],
        tools: List[ToolSpec],
        trace: AgentTrace,
    ) -> ModelEvent:
        payload = build_anthropic_request(
            messages=messages,
            tools=tools,
            model=self.model,
            max_tokens=self.max_tokens,
            system_prompt=self.system_prompt,
        )
        response = await self._client.messages.create(**payload)
        content: List[Dict[str, Any]] = []
        for block in response.content:
            if hasattr(block, "model_dump"):
                content.append(block.model_dump())
            else:  # pragma: no cover - older SDK shapes
                content.append(dict(block))
        return ModelEvent(role="assistant", content=content, stop_reason=response.stop_reason)


# --------------------------------------------------------------------------- #
# Zero-cost local policy model
# --------------------------------------------------------------------------- #
class LocalPolicyModel:
    """Deterministic zero-cost policy emitting Anthropic-shaped events.

    The policy is a compiled list of steps; each step is either a dict
    ``{"tool": name, "input": payload}`` or a callable
    ``fn(trace) -> Optional[dict]`` (returning ``None`` skips the step).
    When steps are exhausted the model returns ``stop_reason == "end_turn"``
    with a final text composed by ``final_text(trace)``.
    """

    def __init__(self, steps: List[Any], final_text: Callable[[AgentTrace], str]) -> None:
        self._steps = list(steps)
        self._final_text = final_text
        self._cursor = 0

    def reset(self) -> None:
        self._cursor = 0

    def next_response(
        self,
        messages: List[Dict[str, Any]],
        tools: List[ToolSpec],
        trace: AgentTrace,
    ) -> ModelEvent:
        while self._cursor < len(self._steps):
            step = self._steps[self._cursor]
            self._cursor += 1
            if callable(step):
                step = step(trace)
            if not step:
                continue
            payload = step.get("input", {})
            block = {
                "type": "tool_use",
                "id": new_id("toolu"),
                "name": step["tool"],
                "input": payload,
            }
            return ModelEvent(role="assistant", content=[block], stop_reason=StopReason.TOOL_USE.value)
        return ModelEvent(
            role="assistant",
            content=[{"type": "text", "text": self._final_text(trace)}],
            stop_reason=StopReason.END_TURN.value,
        )


def make_default_model(steps: List[Any], final_text: Callable[[AgentTrace], str]):
    """Return the native SDK adapter when a key exists, else the zero-cost policy."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return AnthropicSDKAdapter()
        except RuntimeError:
            pass
    return LocalPolicyModel(steps, final_text)


# --------------------------------------------------------------------------- #
# Evidence-novelty quality scoring (plateau breaker input)
# --------------------------------------------------------------------------- #
def parse_result_text(call: Dict[str, Any]) -> Optional[Any]:
    try:
        return json.loads(call.get("result_text", ""))
    except (json.JSONDecodeError, TypeError):
        return None


def default_quality(trace: AgentTrace) -> float:
    """Evidence-novelty score over completed tool calls.

    Rewards successful calls, *novel* evidence artifacts (unique URLs),
    sandboxed computation, dialectical pressure and certified claims;
    penalises error envelopes.  Monotonic growth is the signal the plateau
    breaker watches.
    """
    score = 0.0
    seen_urls: set = set()
    for call in trace.tool_calls:
        if call.get("is_error"):
            score -= 0.25
            continue
        score += 1.0
        parsed = parse_result_text(call)
        if not isinstance(parsed, dict):
            continue
        name = call.get("name")
        if name == "web_search":
            for row in parsed.get("results", []):
                url = row.get("url") if isinstance(row, dict) else None
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    score += 0.2  # novel evidence artifact
        elif name == "extract_page":
            url = parsed.get("url")
            if parsed.get("ok") and url and url not in seen_urls:
                seen_urls.add(url)
                score += 0.5
            elif parsed.get("ok"):
                score += 0.25
        elif name == "run_python":
            if parsed.get("ok"):
                score += 0.3
        elif name == "audit_claim":
            score += float(parsed.get("entailment_probability", 0.0))
            if parsed.get("certified"):
                score += 2.0
        elif name == "dialectic_probe":
            score += 0.4
    return round(score, 4)


# --------------------------------------------------------------------------- #
# The loop (async-native with sync facade)
# --------------------------------------------------------------------------- #
class AgentLoop:
    """Anthropic tool-use state machine with hard circuit breakers."""

    def __init__(
        self,
        dispatcher: ToolDispatcher,
        model: Any,
        max_iterations: int = MAX_ITERATIONS_DEFAULT,
        plateau_patience: int = QUALITY_PLATEAU_PATIENCE,
        quality_fn: Callable[[AgentTrace], float] = default_quality,
    ) -> None:
        self.dispatcher = dispatcher
        self.model = model
        self.max_iterations = int(max_iterations)
        self.plateau_patience = int(plateau_patience)
        self.quality_fn = quality_fn

    # ------------------------------------------------------------------ #
    async def _invoke_model(self, messages: List[Dict[str, Any]], trace: AgentTrace) -> ModelEvent:
        outcome = self.model.next_response(messages, TOOL_SPECS, trace)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return outcome

    # ------------------------------------------------------------------ #
    async def run_async(self, goal: str, node_id: str = "node") -> AgentTrace:
        trace = AgentTrace(node_id=node_id, goal=goal)
        messages: List[Dict[str, Any]] = [{"role": "user", "content": [{"type": "text", "text": goal}]}]
        plateau = 0
        stop_cause: Optional[str] = None

        while trace.iterations < self.max_iterations:
            trace.iterations += 1
            event = await self._invoke_model(messages, trace)
            messages.append({"role": event.role, "content": [dict(block) for block in event.content]})

            if event.stop_reason == StopReason.END_TURN.value:
                stop_cause = StopReason.END_TURN.value
                break

            tool_blocks = [b for b in event.content if b.get("type") == "tool_use"]
            if not tool_blocks:
                stop_cause = StopReason.ERROR.value
                logger.error("assistant turn declared tool_use stop_reason without tool_use blocks")
                break

            result_blocks: List[Dict[str, Any]] = []
            for block in tool_blocks:
                # Tools are blocking (network / subprocess); keep the event
                # loop responsive by dispatching in a worker thread.
                result_text, is_error = await asyncio.to_thread(
                    self.dispatcher.dispatch, block.get("name", ""), block.get("input", {})
                )
                result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.get("id", ""),
                        "content": [{"type": "text", "text": result_text}],
                        "is_error": is_error,
                    }
                )
                trace.tool_calls.append(
                    {
                        "iteration": trace.iterations,
                        "name": block.get("name", ""),
                        "tool_use_id": block.get("id", ""),
                        "input": block.get("input", {}),
                        "result_text": result_text,
                        "is_error": is_error,
                        "timestamp": utc_timestamp(),
                    }
                )
            messages.append({"role": "user", "content": result_blocks})

            quality = self.quality_fn(trace)
            trace.quality_history.append(quality)
            if quality > trace.best_quality:
                trace.best_quality = quality
                plateau = 0
            else:
                plateau += 1
                if plateau >= self.plateau_patience:
                    stop_cause = StopReason.QUALITY_PLATEAU.value
                    logger.info(
                        "node %s: evidence-novelty plateaued for %d turns (score %.3f); stopping",
                        node_id,
                        plateau,
                        quality,
                    )
                    break

        if stop_cause is None:
            stop_cause = StopReason.MAX_ITERATIONS.value
            logger.warning("node %s: hit max_iterations=%d", node_id, self.max_iterations)

        trace.stop_cause = stop_cause
        trace.messages = messages
        trace.duration_s = time.time() - trace.started_at
        return trace

    # ------------------------------------------------------------------ #
    def run(self, goal: str, node_id: str = "node") -> AgentTrace:
        """Synchronous facade over :meth:`run_async`."""
        return asyncio.run(self.run_async(goal, node_id))


# --------------------------------------------------------------------------- #
# Query shapers
# --------------------------------------------------------------------------- #
def research_query(question: str) -> str:
    """Sanitize a natural-language question into a keyword-form search query.

    Strips interrogative scaffolding ("Does", "What is", trailing "?") that
    otherwise derails lexical search engines into matching the question words
    themselves (poka-yoke against retrieval-topic drift).
    """
    text = (question or "").replace("?", " ").strip().lower()
    words = text.split()
    leading_drop = {
        "does",
        "do",
        "did",
        "is",
        "are",
        "was",
        "were",
        "can",
        "could",
        "should",
        "would",
        "will",
        "has",
        "have",
        "had",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        "how",
        "when",
        "where",
        "there",
    }
    while words and words[0] in leading_drop:
        words = words[1:]
    cleaned = " ".join(words)
    return cleaned if cleaned else (question or "").strip()


def compact_query(text: str, max_words: int = 8) -> str:
    """Compress text to its leading content words, order preserved.

    Search backends degrade sharply on long queries (Wikipedia returns zero
    hits beyond ~8 words); this keeps hypotheses and counter-queries potent.
    """
    tokens = [t for t in tokenize(text or "") if t not in STOPWORDS and len(t) > 1]
    kept: List[str] = []
    for token in tokens:
        if token not in kept:
            kept.append(token)
        if len(kept) >= max_words:
            break
    return " ".join(kept) if kept else (text or "").strip()


# --------------------------------------------------------------------------- #
# Script builders for the four DAG task types (zero-cost LocalPolicyModel)
# --------------------------------------------------------------------------- #
def _first_unextracted_url(trace: AgentTrace, skip: int = 0) -> Optional[str]:
    extracted: List[str] = []
    urls: List[str] = []
    for call in trace.tool_calls:
        if call["name"] == "extract_page":
            extracted.append(str(call.get("input", {}).get("url", "")))
        if call["name"] == "web_search":
            parsed = parse_result_text(call)
            if isinstance(parsed, dict):
                for row in parsed.get("results", []):
                    if isinstance(row, dict) and row.get("url"):
                        urls.append(row["url"])
    remaining = [u for u in urls if u not in extracted]
    if skip < len(remaining):
        return remaining[skip]
    return None


def _url_step(skip: int) -> Callable[[AgentTrace], Optional[Dict[str, Any]]]:
    def step(trace: AgentTrace) -> Optional[Dict[str, Any]]:
        url = _first_unextracted_url(trace, skip=skip)
        if not url:
            return None
        return {"tool": "extract_page", "input": {"url": url}}

    return step


def exploration_script(question: str) -> Tuple[List[Any], Callable[[AgentTrace], str]]:
    keyword_query = research_query(question)
    compact = compact_query(keyword_query, max_words=8)
    steps: List[Any] = [
        {"tool": "web_search", "input": {"query": keyword_query, "max_results": 6}},
        # structurally distinct second vector: short content-word form (this
        # is the shape long-query-hostile backends can actually answer)
        {"tool": "web_search", "input": {"query": compact, "max_results": 6}},
        _url_step(0),
        _url_step(1),
    ]

    def final_text(trace: AgentTrace) -> str:
        hits = 0
        pages = 0
        for call in trace.tool_calls:
            parsed = parse_result_text(call)
            if not isinstance(parsed, dict):
                continue
            if call["name"] == "web_search":
                hits += len(parsed.get("results", []))
            if call["name"] == "extract_page" and parsed.get("ok"):
                pages += 1
        return json_dumps({"stage": "EXPLORATION", "search_hits": hits, "pages_extracted": pages})

    return steps, final_text


def extraction_script(question: str, snippets: List[str]) -> Tuple[List[Any], Callable[[AgentTrace], str]]:
    snippet_literals = json_dumps(snippets[:12])
    aggregation_code = (
        "import json, re\n"
        f"snippets = {snippet_literals}\n"
        "numbers = []\n"
        "for text in snippets:\n"
        "    for match in re.findall(r'(?<!\\d)(\\d+(?:\\.\\d+)?)\\s*(?:%|percent|kg|lbs?|pounds|weeks?|months?|years?)', text):\n"
        "        numbers.append(float(match))\n"
        "digest = {\n"
        "    'numeric_values_found': sorted(numbers),\n"
        "    'count': len(numbers),\n"
        "    'mean': round(sum(numbers) / len(numbers), 3) if numbers else None,\n"
        "    'min': min(numbers) if numbers else None,\n"
        "    'max': max(numbers) if numbers else None,\n"
        "}\n"
        "print(json.dumps(digest))\n"
    )
    steps: List[Any] = [
        {"tool": "run_python", "input": {"code": aggregation_code, "timeout": 20}},
        {
            "tool": "web_search",
            "input": {
                "query": compact_query(f"{research_query(question)} statistics results numbers", 8),
                "max_results": 5,
            },
        },
    ]

    def final_text(trace: AgentTrace) -> str:
        return json_dumps({"stage": "DATA_EXTRACTION", "tool_calls": len(trace.tool_calls)})

    return steps, final_text


def falsification_script(hypothesis: str) -> Tuple[List[Any], Callable[[AgentTrace], str]]:
    steps: List[Any] = [
        {
            "tool": "dialectic_probe",
            "input": {"hypothesis": hypothesis, "max_counter_queries": 2},
        }
    ]

    def final_text(trace: AgentTrace) -> str:
        for call in trace.tool_calls:
            if call["name"] == "dialectic_probe":
                parsed = parse_result_text(call)
                if isinstance(parsed, dict):
                    return json_dumps(
                        {
                            "stage": "FALSIFICATION",
                            "verdict": parsed.get("verdict"),
                            "support_score": parsed.get("support_score"),
                            "refute_score": parsed.get("refute_score"),
                        }
                    )
        return json_dumps({"stage": "FALSIFICATION", "verdict": "UNDECIDED"})

    return steps, final_text


def synthesis_script(
    question: str, claims: List[str], sources: List[str]
) -> Tuple[List[Any], Callable[[AgentTrace], str]]:
    steps: List[Any] = []
    for claim in claims[:3]:
        steps.append(
            {
                "tool": "audit_claim",
                "input": {"claim": claim, "sources": sources[:6]},
            }
        )

    def final_text(trace: AgentTrace) -> str:
        audits = []
        for call in trace.tool_calls:
            if call["name"] == "audit_claim":
                parsed = parse_result_text(call)
                if isinstance(parsed, dict):
                    audits.append(
                        {
                            "claim": parsed.get("claim"),
                            "verdict": parsed.get("verdict"),
                            "certified": parsed.get("certified"),
                            "entailment_probability": parsed.get("entailment_probability"),
                        }
                    )
        return json_dumps({"stage": "SYNTHESIS", "question": question, "audits": audits})

    return steps, final_text
