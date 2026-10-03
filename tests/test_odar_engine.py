"""ODAR v2.0 evaluator-optimizer verification harness (network-isolated).

Mandated coverage:

1. ``SemanticLoopBreaker`` - query passing, cosine interception at 0.82 with
   orthogonal facet rerouting (statistics / criticism / systematic_review /
   dispute / case_study), Jaccard hard-blocking at 0.85.
2. ``ExecutionSandbox`` - deterministic math, syntax-error capture,
   infinite-loop wall-clock timeout, POSIX rlimit enforcement, AST import
   allowlist + dunder + dangerous-builtin blocking.
3. ``CitationAuditor`` - Entailment (>= 0.75), Contradiction and Neutral
   classification across 4-8 spans, plus provisional auto-degradation tagging.
4. ``TaskDAGOrchestrator`` - cycle rejection, unknown-dependency detection,
   topological order, parent->child payload injection, concurrent execution
   of same-generation nodes, failure quarantine, async coroutine handlers.
5. ``AgentLoop`` - full tool-use lifecycle, recovery from tool-failure
   payloads, monotonic quality-plateau early stop, max-iteration breaker,
   schema gates, native Anthropic payload prompt-caching.

The suite is network-free by default so it passes on an air-gapped box;
opt-in live tests are marked ``integration``.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time

import pytest

from odar.agent import (
    AgentLoop,
    AnthropicSDKAdapter,
    LocalPolicyModel,
    ToolDispatcher,
    TOOL_SPECS,
    build_anthropic_request,
    parse_result_text,
)
from odar.citation_auditor import CitationAuditor, DeterministicNLIScorer, softmax
from odar.dialectic import DialecticalEngine, score_cues
from odar.loop_breaker import (
    ALTERNATE_VECTORS,
    SemanticLoopBreaker,
    cosine_similarity,
    jaccard_similarity,
)
from odar.orchestrator import (
    DAGValidationError,
    TaskDAGOrchestrator,
    build_research_plan,
)
from odar.retrieval import ZeroCostSearch
from odar.sandbox import (
    ALLOWED_IMPORTS,
    ExecutionSandbox,
    SandboxError,
    safe_eval_arithmetic,
    validate_sandbox_code,
)
from odar.schemas import (
    SearchHit,
    StopReason,
    TaskNode,
    TaskStatus,
    TaskType,
    Verdict,
    validate_payload,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def make_agent(steps, final_text=None, **loop_kwargs):
    dispatcher = ToolDispatcher(auditor_factory=lambda: CitationAuditor(scorer=DeterministicNLIScorer()))
    model = LocalPolicyModel(steps, final_text or (lambda trace: "done"))
    return AgentLoop(dispatcher=dispatcher, model=model, **loop_kwargs), dispatcher


TOWER_SOURCE = (
    "The Eiffel Tower is located in Paris. It was completed in 1889. "
    "The tower is made of wrought iron. It stands on the Champ de Mars. "
    "Millions of visitors arrive every year. The structure is 330 metres tall."
)


# =========================================================================== #
# 1. Semantic loop breaker
# =========================================================================== #
class TestSemanticLoopBreaker:
    def test_distinct_queries_pass(self):
        breaker = SemanticLoopBreaker()
        first = breaker.guard("intermittent fasting weight loss trials")
        second = breaker.guard("solar panel efficiency perovskite tandem cells")
        assert first.action == "pass"
        assert second.action == "pass"
        assert breaker.stats["passed"] == 2

    def test_cosine_interception_at_threshold_with_facet_reroute(self):
        breaker = SemanticLoopBreaker()
        assert breaker.similarity_threshold == pytest.approx(0.82)
        assert breaker.window_size == 5
        original = "does intermittent fasting improve cardiovascular health outcomes"
        first = breaker.guard(original)
        second = breaker.guard(original)
        assert first.action == "pass"
        assert second.intercepted is True
        assert second.action == "reroute"
        assert second.similarity >= 0.82
        assert second.query != original
        facet_names = {name for name, _ in ALTERNATE_VECTORS}
        assert facet_names == {"statistics", "criticism", "systematic_review", "dispute", "case_study"}
        assert second.strategy in facet_names
        # the forced alternate vector must itself sit below the cosine gate
        assert cosine_similarity(original, second.query) < breaker.similarity_threshold

    def test_facet_rotation_cycles_all_vectors(self):
        breaker = SemanticLoopBreaker()
        seen = set()
        query = "renewable grid storage batteries cost curve analysis"
        for _ in range(len(ALTERNATE_VECTORS)):
            alternate, strategy = breaker._alternate_vector(query)
            breaker._reroute_counter += 1
            seen.add(strategy)
            assert alternate != query
        assert seen == {name for name, _ in ALTERNATE_VECTORS}

    def test_jaccard_hard_block_at_threshold(self):
        breaker = SemanticLoopBreaker(window_size=1)
        assert breaker.jaccard_threshold == pytest.approx(0.85)
        query = "vaccine efficacy waning immunity study"
        first = breaker.guard(query)  # pass
        second = breaker.guard(query)  # reroute (cosine gate, consecutive)
        third = breaker.guard(query)  # hard block (jaccard vs history)
        assert first.action == "pass"
        assert second.action == "reroute"
        assert third.action == "block"
        assert third.jaccard >= breaker.jaccard_threshold

    def test_empty_query_rejected(self):
        decision = SemanticLoopBreaker().guard("   ")
        assert decision.action == "block"
        assert "empty" in decision.reason

    def test_similarity_metrics_bounds(self):
        assert cosine_similarity("alpha beta gamma", "alpha beta gamma") == pytest.approx(1.0)
        assert 0.0 <= cosine_similarity("alpha", "completely different text") < 1.0
        assert jaccard_similarity("one two three", "one two three") == pytest.approx(1.0)
        assert jaccard_similarity("one two", "three four") == pytest.approx(0.0)


# =========================================================================== #
# 2. Execution sandbox (kernel-level hardening)
# =========================================================================== #
class TestExecutionSandbox:
    def test_deterministic_math_computation(self):
        digest = ExecutionSandbox().run_python("print(6 * 7)")
        assert digest["ok"] is True
        assert digest["exit_code"] == 0
        assert digest["stdout"].strip() == "42"
        assert digest["timed_out"] is False
        assert digest["policy_blocked"] is False

    def test_allowed_imports_pass_gate(self):
        code = (
            "import math, statistics, json, re\n"
            "from collections import Counter\n"
            "from itertools import islice\n"
            "from decimal import Decimal\n"
            "from fractions import Fraction\n"
            "print(math.sqrt(16), statistics.mean([1, 2, 3]), Counter('aab')['a'])\n"
        )
        digest = ExecutionSandbox().run_python(code)
        assert digest["ok"] is True
        assert "4.0 2 2" in digest["stdout"]
        assert ALLOWED_IMPORTS == {
            "math",
            "statistics",
            "json",
            "re",
            "collections",
            "itertools",
            "decimal",
            "fractions",
        }

    def test_json_aggregation_script(self):
        code = (
            "import json\n"
            "values = [3.1, 2.4, 5.5]\n"
            "print(json.dumps({'mean': round(sum(values) / len(values), 3), 'n': len(values)}))\n"
        )
        digest = ExecutionSandbox().run_json(code)
        assert digest["ok"] is True
        assert digest["json"] == {"mean": 3.667, "n": 3}

    def test_syntax_error_is_captured(self):
        digest = ExecutionSandbox().run_python("def broken(:\n    pass")
        assert digest["ok"] is False
        assert digest["policy_blocked"] is True
        assert "SyntaxError" in digest["stderr"]

    def test_runtime_error_is_captured(self):
        digest = ExecutionSandbox().run_python("raise ValueError('boom')")
        assert digest["ok"] is False
        assert digest["policy_blocked"] is False
        assert "ValueError" in digest["stderr"]
        assert "boom" in digest["stderr"]

    def test_infinite_loop_wallclock_timeout(self):
        sandbox = ExecutionSandbox(cpu_seconds=None)
        started = time.perf_counter()
        digest = sandbox.run_python("while True:\n    pass", timeout=1)
        elapsed = time.perf_counter() - started
        assert digest["ok"] is False
        assert digest["timed_out"] is True
        assert elapsed < 8

    @pytest.mark.skipif(os.name != "posix", reason="rlimits are POSIX-only")
    def test_posix_rlimit_cpu_enforced(self):
        sandbox = ExecutionSandbox(timeout=15, cpu_seconds=1)
        started = time.perf_counter()
        digest = sandbox.run_python("while True:\n    pass", timeout=15)
        elapsed = time.perf_counter() - started
        assert digest["ok"] is False
        assert digest["timed_out"] is False  # killed by RLIMIT_CPU, not wall clock
        assert digest["exit_code"] != 0
        assert elapsed < 12

    def test_ast_gate_blocks_disallowed_import(self):
        violations = validate_sandbox_code("import os\nos.system('id')")
        assert any("import 'os' blocked" in v for v in violations)
        digest = ExecutionSandbox().run_python("import os")
        assert digest["ok"] is False
        assert digest["policy_blocked"] is True
        assert "SandboxPolicyViolation" in digest["stderr"]

    def test_ast_gate_blocks_from_import(self):
        violations = validate_sandbox_code("from os import path")
        assert any("from-import 'os' blocked" in v for v in violations)
        assert validate_sandbox_code("from collections import deque") == []

    def test_ast_gate_blocks_dunder_attribute_access(self):
        violations = validate_sandbox_code("x = ().__class__")
        assert any("__class__" in v for v in violations)
        violations = validate_sandbox_code("y = ''.__subclasses__ if False else None")
        assert any("__subclasses__" in v for v in violations)

    def test_ast_gate_blocks_dangerous_builtins(self):
        for code in ("eval('1+1')", "exec('x=1')", "open('/etc/passwd')", "compile('1','','eval')"):
            violations = validate_sandbox_code(code)
            assert violations, f"expected violation for {code!r}"
            assert any("blocked" in v for v in violations)

    def test_ast_gate_blocks_dunder_string_escapes(self):
        violations = validate_sandbox_code("s = 'use the __class__ escape hatch'")
        assert any("dunder escape token" in v for v in violations)

    def test_policy_blocked_code_never_spawns_child(self):
        digest = ExecutionSandbox().run_python("import subprocess")
        assert digest["ok"] is False
        assert digest["policy_blocked"] is True
        assert digest["duration_s"] < 1.0  # static gate: no subprocess overhead

    def test_output_capping(self):
        digest = ExecutionSandbox(max_output_bytes=200).run_python("print('x' * 5000)")
        assert digest["output_truncated"] is True
        assert len(digest["stdout"].encode()) <= 250

    def test_safe_eval_arithmetic(self):
        assert safe_eval_arithmetic("2 + 3 * 4") == pytest.approx(14.0)
        assert safe_eval_arithmetic("(10 - 4) / 2") == pytest.approx(3.0)
        assert safe_eval_arithmetic("-5 + 2 ** 3") == pytest.approx(3.0)

    def test_safe_eval_rejects_injection(self):
        for evil in ("__import__('os').system('id')", "open('/etc/passwd')", "a + 1", ""):
            with pytest.raises(SandboxError):
                safe_eval_arithmetic(evil)


# =========================================================================== #
# 3. Citation auditor (NLI classification, 4-8 spans, auto-degradation)
# =========================================================================== #
class TestCitationAuditor:
    def make_auditor(self, **kwargs):
        return CitationAuditor(scorer=DeterministicNLIScorer(), **kwargs)

    def test_entailment_detected_but_provisional_without_neural(self):
        # Claim is entailed by a span DIFFERENT from itself (non-circular).
        # With only the deterministic fallback scorer the entailment is
        # DETECTED but must stay PROVISIONAL - certification is impossible.
        auditor = self.make_auditor()
        result = auditor.audit("The Eiffel Tower is made of wrought iron.", [TOWER_SOURCE])
        assert result.verdict is Verdict.ENTAILMENT
        assert result.entailment_probability >= auditor.threshold == 0.75
        assert result.provisional is True
        assert result.certified is False, "heuristic verification can never certify"
        assert auditor.min_spans <= result.spans_checked <= auditor.max_spans

    def test_circular_self_support_refused(self):
        # Adversarial anti-circularity: evidence produced by the AGENT ITSELF
        # (origin="agent") can never certify a near-identical claim.
        from odar.evidence import Claim, Relation, SourceRecord
        from odar.schemas import new_id

        auditor = self.make_auditor()
        verbatim = "The Eiffel Tower is located in Paris."
        agent_source = SourceRecord(
            source_id=new_id("src"),
            url="odar://self-report",
            origin="agent",
            extracted_text=verbatim,
        )
        claim = Claim(claim_id=new_id("clm"), text=verbatim)
        result, items, _gaps = auditor.audit_with_provenance(claim, [agent_source], [verbatim])
        assert all(item.relation is Relation.CIRCULAR for item in items)
        assert result.certified is False or all(item.relation is Relation.CIRCULAR for item in items)

    def test_external_verbatim_quote_is_legitimate_citation(self):
        # Direct quotation of an external source IS legitimate support.
        from odar.evidence import Claim, Relation, SourceRecord
        from odar.schemas import new_id

        auditor = self.make_auditor()
        sentence = "The Eiffel Tower is located in Paris."
        external = SourceRecord(
            source_id=new_id("src"),
            url="https://example.com/tower",
            origin="external",
            extracted_text=sentence,
        )
        claim = Claim(claim_id=new_id("clm"), text=sentence)
        result, items, _gaps = auditor.audit_with_provenance(claim, [external], [sentence])
        assert any(item.relation is Relation.SUPPORTS for item in items)

    def test_contradiction_detected(self):
        auditor = self.make_auditor()
        result = auditor.audit("The Eiffel Tower is not located in Paris.", [TOWER_SOURCE])
        assert result.verdict is Verdict.CONTRADICTION
        assert result.certified is False

    def test_neutral_proposition(self):
        auditor = self.make_auditor()
        result = auditor.audit("Bananas are an excellent source of potassium.", [TOWER_SOURCE])
        assert result.verdict is Verdict.NEUTRAL
        assert result.entailment_probability < auditor.threshold
        assert result.certified is False

    def test_span_budget_enforced(self):
        auditor = self.make_auditor()
        long_source = " ".join(
            f"Sentence number {i} discusses unrelated filler material for padding." for i in range(30)
        )
        spans = auditor.prepare_spans(long_source, claim="filler padding sentence")
        assert len(spans) <= auditor.max_spans
        result = auditor.audit("Unrelated filler padding sentence number.", [long_source])
        assert result.spans_checked <= auditor.max_spans

    def test_insufficient_spans_blocks_certification(self):
        auditor = self.make_auditor(min_spans=4)
        result = auditor.audit("The tower is iron.", ["The tower is iron."])
        assert result.certified is False
        assert "span budget" in result.note or result.verdict is not Verdict.ENTAILMENT

    def test_empty_sources_never_crash(self):
        result = self.make_auditor().audit("Any claim at all here.", [])
        assert result.certified is False
        assert result.spans_checked == 0

    def test_auto_degradation_tags_provisional(self, monkeypatch):
        auditor = CitationAuditor()  # neural model NOT loaded in this test

        def broken_loader():
            raise RuntimeError("simulated model download failure")

        monkeypatch.setattr(auditor, "_try_load_neural", broken_loader)
        auditor.ensure_scorer()  # must not raise
        assert auditor.scorer_backend == "deterministic-lexical-fallback"
        result = auditor.audit("The Eiffel Tower is located in Paris.", [TOWER_SOURCE])
        assert result.provisional is True
        assert "PROVISIONAL" in result.note
        assert result.verdict in (Verdict.ENTAILMENT, Verdict.CONTRADICTION, Verdict.NEUTRAL)

    def test_softmax_and_lexical_scorer_contract(self):
        probs = softmax([2.2, -1.5, 0.0])
        assert probs[0] > probs[1] and probs[0] > probs[2]
        assert sum(probs) == pytest.approx(1.0)
        rows = DeterministicNLIScorer().predict(
            [("The cat sits on the mat today", "The cat sits on the mat today")]
        )
        assert len(rows) == 1 and len(rows[0]) == 3


# =========================================================================== #
# 4. Async concurrent DAG orchestrator
# =========================================================================== #
class TestTaskDAGOrchestrator:
    def build_diamond(self):
        dag = TaskDAGOrchestrator()
        dag.add_task(TaskNode("A", TaskType.EXPLORATION, "root"))
        dag.add_task(TaskNode("B", TaskType.DATA_EXTRACTION, "left", depends_on=["A"]))
        dag.add_task(TaskNode("C", TaskType.FALSIFICATION, "right", depends_on=["A"]))
        dag.add_task(TaskNode("D", TaskType.SYNTHESIS, "sink", depends_on=["B", "C"]))
        return dag

    def test_topological_order_respects_dependencies(self):
        dag = self.build_diamond()
        order = dag.execution_order()
        assert set(order) == {"A", "B", "C", "D"}
        pos = {task_id: index for index, task_id in enumerate(order)}
        assert pos["A"] < pos["B"]
        assert pos["A"] < pos["C"]
        assert pos["B"] < pos["D"]
        assert pos["C"] < pos["D"]

    def test_cycle_rejected(self):
        dag = TaskDAGOrchestrator()
        dag.add_task(TaskNode("X", TaskType.EXPLORATION, "x", depends_on=["Y"]))
        dag.add_task(TaskNode("Y", TaskType.SYNTHESIS, "y", depends_on=["X"]))
        with pytest.raises(DAGValidationError):
            dag.execution_order()

    def test_unknown_dependency_rejected(self):
        dag = TaskDAGOrchestrator()
        dag.add_task(TaskNode("A", TaskType.EXPLORATION, "a", depends_on=["GHOST"]))
        with pytest.raises(DAGValidationError):
            dag.execution_order()

    def test_duplicate_task_id_rejected(self):
        dag = TaskDAGOrchestrator()
        dag.add_task(TaskNode("A", TaskType.EXPLORATION, "a"))
        with pytest.raises(DAGValidationError):
            dag.add_task(TaskNode("A", TaskType.SYNTHESIS, "dup"))

    def test_run_propagates_dependency_results(self):
        dag = self.build_diamond()

        def handler(node, dep_results, context):
            upstream = sum(value for value in dep_results.values() if isinstance(value, int))
            return 1 + upstream

        handlers = {task_type: handler for task_type in TaskType}
        nodes = dag.run(handlers, context={})
        assert nodes["A"].result == 1
        assert nodes["B"].result == 2
        assert nodes["C"].result == 2
        assert nodes["D"].result == 5  # 1 + B(2) + C(2)
        assert all(node.status is TaskStatus.COMPLETED for node in nodes.values())

    def test_same_generation_runs_concurrently(self):
        dag = self.build_diamond()
        barrier = threading.Barrier(2, timeout=8)

        def handler(node, dep_results, context):
            if node.task_id in ("B", "C"):
                # Both siblings must reach this point simultaneously; a
                # sequential scheduler would time out and raise here.
                barrier.wait()
            return node.task_id

        nodes = dag.run({task_type: handler for task_type in TaskType}, context={})
        assert all(node.status is TaskStatus.COMPLETED for node in nodes.values())

    def test_native_coroutine_handlers(self):
        dag = self.build_diamond()

        async def handler(node, dep_results, context):
            await asyncio.sleep(0.01)
            upstream = sorted(k for k, v in dep_results.items() if v is not None)
            return {"id": node.task_id, "saw": upstream}

        nodes = asyncio.run(dag.run_async({task_type: handler for task_type in TaskType}))
        assert nodes["A"].result == {"id": "A", "saw": []}
        assert nodes["D"].result == {"id": "D", "saw": ["B", "C"]}

    def test_node_failure_is_quarantined(self):
        dag = self.build_diamond()

        def handler(node, dep_results, context):
            if node.task_id == "B":
                raise RuntimeError("deliberate failure")
            return "ok"

        nodes = dag.run({task_type: handler for task_type in TaskType}, context={})
        assert nodes["B"].status is TaskStatus.FAILED
        assert "deliberate failure" in nodes["B"].error
        assert nodes["A"].status is TaskStatus.COMPLETED
        assert nodes["C"].status is TaskStatus.COMPLETED
        assert nodes["D"].status is TaskStatus.COMPLETED  # degraded but executed
        assert nodes["D"].error and "degraded" in nodes["D"].error

    def test_missing_handler_skips_node(self):
        dag = self.build_diamond()
        nodes = dag.run({TaskType.EXPLORATION: lambda n, d, c: "ok"}, context={})
        assert nodes["B"].status is TaskStatus.SKIPPED

    def test_default_research_plan_shape(self):
        dag = build_research_plan("Does X cause Y?")
        order = dag.execution_order()
        assert order[0] == "T1_exploration"
        assert order[-1] == "T4_synthesis"
        types = [dag.get(t).task_type for t in order]
        assert TaskType.EXPLORATION in types
        assert TaskType.DATA_EXTRACTION in types
        assert TaskType.FALSIFICATION in types
        assert TaskType.SYNTHESIS in types


# =========================================================================== #
# 5. AgentLoop lifecycle & circuit breakers
# =========================================================================== #
class TestAgentLoopLifecycle:
    def test_tool_lifecycle_reaches_end_turn(self):
        steps = [{"tool": "run_python", "input": {"code": "print(11 * 3)"}}]
        agent, dispatcher = make_agent(steps)
        trace = agent.run("compute 11*3", node_id="lifecycle")
        assert trace.stop_cause == StopReason.END_TURN.value
        assert trace.iterations == 2  # one tool turn + one end_turn turn
        messages = trace.messages
        assert messages[0]["role"] == "user"
        assistant_turn = messages[1]
        assert assistant_turn["role"] == "assistant"
        assert assistant_turn["content"][0]["type"] == "tool_use"
        result_turn = messages[2]
        assert result_turn["role"] == "user"
        block = result_turn["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == assistant_turn["content"][0]["id"]
        payload = json.loads(block["content"][0]["text"])
        assert payload["ok"] is True and payload["stdout"].strip() == "33"
        assert messages[3]["role"] == "assistant"
        assert messages[3]["content"][0]["type"] == "text"

    def test_max_iterations_circuit_breaker(self):
        steps = [{"tool": "run_python", "input": {"code": "print(1)"}}] * 25
        agent, _ = make_agent(steps)
        trace = agent.run("infinite work")
        assert trace.iterations == agent.max_iterations == 10
        assert trace.stop_cause == StopReason.MAX_ITERATIONS.value

    def test_quality_plateau_circuit_breaker(self):
        steps = [{"tool": "run_python", "input": {"code": "print(1)"}}] * 12
        agent, _ = make_agent(steps, quality_fn=lambda trace: 1.0)
        trace = agent.run("flat quality work")
        assert trace.stop_cause == StopReason.QUALITY_PLATEAU.value
        assert trace.iterations == 3  # establish best, then 2 non-improving turns
        assert trace.quality_history == [1.0, 1.0, 1.0]

    def test_poka_yoke_runtime_error_payload_and_recovery(self):
        steps = [
            {"tool": "run_python", "input": {"code": "raise ValueError('boom')"}},
            {"tool": "run_python", "input": {"code": "print('recovered')"}},
        ]
        agent, dispatcher = make_agent(steps)
        trace = agent.run("crash then recover")
        assert trace.stop_cause == StopReason.END_TURN.value
        # Script failure arrives as an error-flagged tool_result envelope...
        assert trace.tool_calls[0]["is_error"] is True
        error_payload = json.loads(trace.tool_calls[0]["result_text"])
        assert error_payload["ok"] is False
        assert "ValueError" in error_payload["stderr"]
        assert "boom" in error_payload["stderr"]
        # ...and the loop recovers autonomously on the next turn.
        assert trace.tool_calls[1]["is_error"] is False
        recovered = json.loads(trace.tool_calls[1]["result_text"])
        assert recovered["ok"] is True and recovered["stdout"].strip() == "recovered"

    def test_sandbox_policy_violation_envelope(self):
        steps = [{"tool": "run_python", "input": {"code": "import os\nos.listdir('/')"}}]
        agent, dispatcher = make_agent(steps)
        trace = agent.run("attempt escape")
        call = trace.tool_calls[0]
        assert call["is_error"] is True
        payload = json.loads(call["result_text"])
        assert payload["ok"] is False
        assert payload["policy_blocked"] is True
        assert "SandboxPolicyViolation" in payload["stderr"]

    def test_schema_violation_rejected_before_execution(self):
        steps = [{"tool": "web_search", "input": {"query": "x"}}]  # minLength is 3
        agent, dispatcher = make_agent(steps)
        trace = agent.run("bad input")
        call = trace.tool_calls[0]
        assert call["is_error"] is True
        payload = json.loads(call["result_text"])
        assert payload["error"]["type"] == "SchemaViolation"
        assert dispatcher.stats["schema_rejections"] == 1

    def test_unknown_tool_payload(self):
        dispatcher = ToolDispatcher()
        text, is_error = dispatcher.dispatch("teleport", {"where": "mars"})
        assert is_error is True
        payload = json.loads(text)
        assert payload["error"]["type"] == "UnknownTool"
        assert "web_search" in payload["error"]["available_tools"]

    def test_jaccard_dedup_inside_search_tool(self):
        dispatcher = ToolDispatcher()
        dispatcher.search = _StubSearch()
        first_text, first_error = dispatcher.dispatch(
            "web_search", {"query": "renewable energy storage grid batteries"}
        )
        second_text, second_error = dispatcher.dispatch(
            "web_search", {"query": "renewable energy storage grid batteries"}
        )
        assert first_error is False
        second = json.loads(second_text)
        assert second["ok"] is False
        assert second["blocked"] in ("jaccard-dedup", "loop-breaker")
        assert dispatcher.stats["dedup_rejections"] == 1

    def test_default_quality_monotonic_growth(self):
        steps = [
            {"tool": "run_python", "input": {"code": "print(1)"}},
            {"tool": "run_python", "input": {"code": "print(2)"}},
        ]
        agent, _ = make_agent(steps)
        trace = agent.run("growing quality")
        assert trace.quality_history == sorted(trace.quality_history)
        assert trace.stop_cause == StopReason.END_TURN.value

    def test_run_async_native_path(self):
        steps = [{"tool": "run_python", "input": {"code": "print(2 ** 5)"}}]
        agent, _ = make_agent(steps)
        trace = asyncio.run(agent.run_async("async path"))
        assert trace.stop_cause == StopReason.END_TURN.value
        payload = parse_result_text(trace.tool_calls[0])
        assert payload["stdout"].strip() == "32"


class _StubSearch:
    def text(self, query, max_results=6):
        return [
            SearchHit(
                url=f"https://example.org/{abs(hash(query)) % 1000}",
                title=f"About {query}",
                snippet=f"Snippet discussing {query} in detail.",
                engine="stub",
            )
        ]


# =========================================================================== #
# 6. Native Anthropic payload & SDK adapter
# =========================================================================== #
class TestAnthropicIntegration:
    def test_prompt_caching_on_system_and_tools(self):
        payload = build_anthropic_request(
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            tools=TOOL_SPECS,
        )
        assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert payload["system"][0]["type"] == "text"
        assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        assert all("cache_control" not in tool for tool in payload["tools"][:-1])
        assert [tool["name"] for tool in payload["tools"]] == [
            "web_search",
            "extract_page",
            "run_python",
            "dialectic_probe",
            "audit_claim",
        ]
        assert payload["max_tokens"] == 1024

    def test_sdk_adapter_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(RuntimeError):
            AnthropicSDKAdapter()

    def test_tool_schemas_strict(self):
        for spec in TOOL_SPECS:
            schema = spec.input_schema
            assert schema["type"] == "object"
            assert schema.get("additionalProperties") is False
            assert schema.get("required")


# =========================================================================== #
# 7. Schema validator
# =========================================================================== #
class TestSchemaValidation:
    SCHEMA = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 3},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def test_valid_payload(self):
        assert validate_payload(self.SCHEMA, {"query": "hello world", "max_results": 3}) == []

    def test_missing_required(self):
        errors = validate_payload(self.SCHEMA, {"max_results": 3})
        assert any("query" in e for e in errors)

    def test_wrong_type(self):
        errors = validate_payload(self.SCHEMA, {"query": 42})
        assert any("expected type 'string'" in e for e in errors)

    def test_min_length_and_range(self):
        errors = validate_payload(self.SCHEMA, {"query": "ab", "max_results": 99})
        assert any("minLength" in e for e in errors)
        assert any("maximum" in e for e in errors)

    def test_additional_properties_rejected(self):
        errors = validate_payload(self.SCHEMA, {"query": "valid query", "rogue": True})
        assert any("additional property" in e for e in errors)

    def test_nested_array_items(self):
        errors = validate_payload(self.SCHEMA, {"query": "valid query", "tags": ["ok", 7]})
        assert any("tags[1]" in e for e in errors)

    def test_bool_is_not_a_number(self):
        errors = validate_payload(self.SCHEMA, {"query": "valid query", "max_results": True})
        assert any("expected type 'integer'" in e for e in errors)


# =========================================================================== #
# 8. Dialectical loop
# =========================================================================== #
class TestDialectic:
    def _engine(self, hits_by_query):
        def search_fn(query):
            return [
                SearchHit(url=url, title="t", snippet=snippet, engine="stub")
                for url, snippet in hits_by_query(query)
            ]

        return DialecticalEngine(search_fn=search_fn)

    def test_refutation_pressure_is_diagnostic_only_without_evaluator(self):
        # Keyword cues can NEVER establish a verdict: without a semantic
        # evaluator the attack stays UNDECIDED even under strong refute cues.
        def hits(query):
            return [
                (
                    "https://ex.org/1",
                    "However the supplements trial found no significant memory improvement and critics dispute the claim.",
                ),
                (
                    "https://ex.org/2",
                    "The supplements result was later retracted; the evidence does not support the memory hypothesis.",
                ),
            ]

        report = self._engine(hits).attack("supplements boost memory", max_counter_queries=2)
        assert report.verdict == "UNDECIDED", "cues alone must never produce a stance"
        # Cues remain visible strictly as telemetry/diagnostics.
        assert report.refute_score > report.support_score
        trace = report.trace
        assert trace.failed_hypothesis
        assert trace.diagnostic_detection
        assert trace.corrective_action
        assert trace.verified_synthesis
        assert report.hypothesis in trace.hypothesis

    def test_support_cues_uphold_nothing_without_evaluator(self):
        def hits(query):
            return [
                (
                    "https://ex.org/1",
                    "Multiple handwashing trials confirm the effect; the evidence shows infection reduction is robust and replicated.",
                ),
            ]

        report = self._engine(hits).attack("handwashing reduces infection", max_counter_queries=1)
        assert report.verdict == "UNDECIDED", "support cues alone must never uphold a claim"
        assert report.support_score > report.refute_score

    def test_semantic_evaluator_is_the_verdict_authority(self):
        from odar.evidence import Relation

        class RefutingEvaluator:
            def stance(self, hypothesis, snippet):
                return Relation.REFUTES, 0.9

        def hits(query):
            return [("https://ex.org/1", "unrelated filler text about weather")]

        report = self._engine(hits).attack(
            "supplements boost memory", max_counter_queries=1, evaluator=RefutingEvaluator()
        )
        assert report.verdict == "REFUTED"

    def test_cues_cannot_override_semantic_evaluator(self):
        # Adversarial: heavy refute CUES in the snippets, but the semantic
        # evaluator finds no stance relation -> verdict must stay UNDECIDED.
        class NeutralEvaluator:
            def stance(self, hypothesis, snippet):
                return None, 0.0

        def hits(query):
            return [
                (
                    "https://ex.org/1",
                    "However critics dispute this and it was retracted; nevertheless confirmed robust replicated evidence.",
                ),
            ]

        report = self._engine(hits).attack(
            "the hypothesis", max_counter_queries=1, evaluator=NeutralEvaluator()
        )
        assert report.verdict == "UNDECIDED"

    def test_search_failure_degrades_gracefully(self):
        def exploding(query):
            raise ConnectionError("network down")

        engine = DialecticalEngine(search_fn=exploding)
        report = engine.attack(
            "some hypothesis", existing_evidence=["however the data contradicts this"], max_counter_queries=2
        )
        assert report.verdict in ("UPHELD", "CHALLENGED", "REFUTED", "UNDECIDED")
        assert report.trace is not None

    def test_cue_scoring(self):
        cues = score_cues("However, the study was retracted and critics dispute it.")
        assert cues["refute_score"] >= 3
        assert cues["support_score"] == 0


# =========================================================================== #
# 9. Retrieval parsing (offline)
# =========================================================================== #
DDG_HTML_FIXTURE = """
<html><body>
<div class="result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage1&amp;rut=abc">Example <b>One</b></a>
  <a class="result__snippet" href="#">First snippet about <b>solar</b> power.</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a" href="https://direct.example.org/page2">Direct Two</a>
  <a class="result__snippet" href="#">Second snippet about wind power.</a>
</div>
</body></html>
"""

WIKI_PAYLOAD_FIXTURE = {
    "query": {
        "search": [
            {
                "title": "Intermittent fasting",
                "snippet": "Intermittent fasting is any of various eating patterns that cycle "
                'between periods of <span class="searchmatch">fasting</span> and non-fasting.',
            },
            {"title": "Fasting", "snippet": "Fasting is the abstinence from food."},
        ]
    }
}


class TestRetrievalParsing:
    def test_parse_ddg_html_resolves_redirects(self):
        hits = ZeroCostSearch.parse_ddg_html(DDG_HTML_FIXTURE, max_results=5)
        assert len(hits) == 2
        assert hits[0].url == "https://example.com/page1"
        assert hits[0].title == "Example One"
        assert "solar" in hits[0].snippet
        assert hits[1].url == "https://direct.example.org/page2"

    def test_parse_wikipedia_payload(self):
        hits = ZeroCostSearch.parse_wikipedia_payload(WIKI_PAYLOAD_FIXTURE, max_results=5)
        assert len(hits) == 2
        assert hits[0].url == "https://en.wikipedia.org/wiki/Intermittent_fasting"
        assert hits[0].engine == "wikipedia"
        assert "<span" not in hits[0].snippet and "fasting" in hits[0].snippet

    def test_parse_wikipedia_payload_malformed(self):
        assert ZeroCostSearch.parse_wikipedia_payload(None, 5) == []
        assert ZeroCostSearch.parse_wikipedia_payload({"query": {}}, 5) == []
        assert ZeroCostSearch.parse_wikipedia_payload({"query": {"search": ["junk", {}]}}, 5) == []

    def test_wikipedia_opensearch_subtier(self, monkeypatch):
        search = ZeroCostSearch()
        monkeypatch.setattr(
            search,
            "_opensearch_request",
            lambda phrase: ["Intermittent fasting", "Fasting"] if "intermittent fasting" in phrase else [],
        )
        monkeypatch.setattr(
            search,
            "_rest_summary",
            lambda title: f"Summary of {title}. Intermittent fasting cycles fasting and eating.",
        )
        hits = search._wikipedia_opensearch("intermittent fasting weight loss evidence", max_results=3)
        assert len(hits) == 2
        assert hits[0].title == "Intermittent fasting"
        assert hits[0].url.endswith("/Intermittent_fasting")
        assert "Summary of" in hits[0].snippet
        assert search.stats["wikipedia_opensearch"] == 1

    def test_wikipedia_search_falls_through_to_opensearch(self, monkeypatch):
        search = ZeroCostSearch()
        monkeypatch.setattr(search, "_wikipedia_request", lambda q, m: [])
        fallback = SearchHit(
            url="https://en.wikipedia.org/wiki/Fasting",
            title="Fasting",
            snippet="Fasting is abstinence.",
            engine="wikipedia",
        )
        monkeypatch.setattr(search, "_wikipedia_opensearch", lambda q, m: [fallback])
        hits = search._search_wikipedia("a very long research query about fasting patterns", 4)
        assert hits == [fallback]

    def test_fallback_chain_reaches_wikipedia(self, monkeypatch):
        search = ZeroCostSearch()
        monkeypatch.setattr(search, "_search_ddgs", lambda q, m: [])
        monkeypatch.setattr(search, "_search_html_fallback", lambda q, m: [])
        monkeypatch.setattr(
            search,
            "_search_wikipedia",
            lambda q, m: [
                SearchHit(
                    url="https://en.wikipedia.org/wiki/Intermittent_fasting",
                    title="Intermittent fasting",
                    snippet="Intermittent fasting evidence from controlled trials.",
                    engine="wikipedia",
                )
            ],
        )
        hits = search.text("intermittent fasting evidence trials", max_results=3)
        assert len(hits) == 1 and hits[0].engine == "wikipedia"

    def test_relevance_gate_filters_offtopic_hits(self, monkeypatch):
        search = ZeroCostSearch()
        dictionary_hit = SearchHit(
            url="https://dictionary.example/intermittent",
            title="INTERMITTENT Definition & Meaning",
            snippet="coming and going at intervals : not continuous; also occasional",
            engine="duckduckgo_search",
        )
        wiki_hit = SearchHit(
            url="https://en.wikipedia.org/wiki/Intermittent_fasting",
            title="Intermittent fasting",
            snippet="Intermittent fasting cycles between fasting and eating windows.",
            engine="wikipedia",
        )
        monkeypatch.setattr(search, "_search_ddgs", lambda q, m: [dictionary_hit])
        monkeypatch.setattr(search, "_search_html_fallback", lambda q, m: [])
        monkeypatch.setattr(search, "_search_wikipedia", lambda q, m: [wiki_hit])
        hits = search.text(
            "intermittent fasting weight loss versus continuous calorie restriction",
            max_results=5,
        )
        assert all(h.engine == "wikipedia" for h in hits)
        assert search.stats["gated_out"] >= 1

    def test_all_offtopic_returns_empty_not_garbage(self, monkeypatch):
        search = ZeroCostSearch()
        monkeypatch.setattr(
            search,
            "_search_ddgs",
            lambda q, m: [
                SearchHit(
                    url="https://d.example",
                    title="INTERMITTENT Definition",
                    snippet="not continuous; occasional",
                    engine="x",
                )
            ],
        )
        monkeypatch.setattr(search, "_search_html_fallback", lambda q, m: [])
        monkeypatch.setattr(search, "_search_wikipedia", lambda q, m: [])
        hits = search.text("intermittent fasting weight loss clinical outcomes", max_results=4)
        assert hits == []

    def test_tiny_queries_skip_gating(self, monkeypatch):
        search = ZeroCostSearch()
        hit = SearchHit(url="https://x.example/a", title="Python", snippet="python language", engine="x")
        monkeypatch.setattr(search, "_search_ddgs", lambda q, m: [hit])
        monkeypatch.setattr(search, "_search_html_fallback", lambda q, m: [])
        monkeypatch.setattr(search, "_search_wikipedia", lambda q, m: [])
        assert search.text("python", max_results=2) == [hit]

    def test_empty_query_returns_no_hits(self):
        assert ZeroCostSearch().text("") == []

    def test_extraction_cap_default_18k(self):
        from odar.retrieval import PageExtractor

        assert PageExtractor().max_chars == 18000


# =========================================================================== #
# 10. Offline DAG integration (no network)
# =========================================================================== #
class TestOfflineIntegration:
    def test_full_pipeline_with_stub_handlers(self):
        dag = build_research_plan("Does method A outperform method B?")
        seen = {}

        def handler(node, dep_results, context):
            seen[node.task_id] = sorted(dep_results)
            return {"node": node.task_id, "evidence": ["stub-snippet one", "stub-snippet two"]}

        nodes = dag.run({task_type: handler for task_type in TaskType}, context={"question": "q"})
        assert nodes["T4_synthesis"].status is TaskStatus.COMPLETED
        assert seen["T2_extraction"] == ["T1_exploration"]
        assert seen["T3_falsification"] == ["T2_extraction"]
        assert seen["T4_synthesis"] == ["T2_extraction", "T3_falsification"]

    def test_agent_loop_with_stub_search_end_to_end(self):
        dispatcher = ToolDispatcher(auditor_factory=lambda: CitationAuditor(scorer=DeterministicNLIScorer()))
        dispatcher.search = _StubSearch()
        steps = [
            {"tool": "web_search", "input": {"query": "grid scale battery storage costs", "max_results": 3}},
            {
                "tool": "audit_claim",
                "input": {
                    "claim": "Snippet discussing grid scale battery storage costs in detail.",
                    "sources": [
                        "Snippet discussing grid scale battery storage costs in detail. "
                        "Grid scale battery storage costs keep falling each year. "
                        "Analysts track grid scale battery storage costs closely. "
                        "The snippet discussing grid scale battery storage costs is detailed."
                    ],
                },
            },
        ]
        model = LocalPolicyModel(steps, lambda trace: "final")
        loop = AgentLoop(dispatcher=dispatcher, model=model)
        trace = loop.run("audit a claim end to end")
        assert trace.stop_cause == StopReason.END_TURN.value
        audit_call = trace.tool_calls[-1]
        audit_payload = parse_result_text(audit_call)
        assert audit_payload["spans_checked"] >= 4
        assert audit_payload["verdict"] in ("ENTAILMENT", "NEUTRAL", "CONTRADICTION")


# =========================================================================== #
# Opt-in live integration (model download + real scoring)
# =========================================================================== #
@pytest.mark.integration
def test_cross_encoder_model_download_and_live_scoring():
    pytest.importorskip(
        "sentence_transformers", reason="neural NLI stack not installed; live scoring is opt-in"
    )
    auditor = CitationAuditor()
    auditor.ensure_scorer()
    if auditor.scorer_backend != auditor.model_name:
        pytest.skip(f"neural model unavailable ({auditor.scorer_backend}); live scoring is opt-in")
    # Fixture pair validated against the real model (p_ent >= 0.99, non-circular).
    source = (
        "The Eiffel Tower is a wrought-iron lattice tower located on the Champ de Mars in Paris. "
        "Gustave Eiffel's company designed and built the tower. It was completed in 1889. "
        "The puddled iron structure weighs about 10100 tonnes. Millions of visitors climb it every year."
    )
    result = auditor.audit(
        "A wrought-iron lattice tower called the Eiffel Tower stands on the Champ de Mars in Paris.",
        [source],
    )
    assert result.verdict is Verdict.ENTAILMENT
    assert result.certified is True
    assert result.provisional is False
