"""Asynchronous concurrent DAG orchestration over NetworkX (ODAR v2.0).

* Research questions are decomposed into a typed directed acyclic graph
  (:class:`odar.schemas.TaskType` nodes).
* The graph is validated (no duplicate ids, no unknown dependencies, no
  cycles) and then executed strictly topologically: **independent nodes in
  the same topological generation run concurrently via ``asyncio.gather``**,
  while every child awaits full resolution of its parents and ingests the
  aggregated parent payloads.
* Handlers may be sync functions (run in a worker thread via
  ``asyncio.to_thread``) or native coroutines.
* Poka-yoke failure isolation: an unrecoverable handler exception is
  quarantined - the node is marked ``FAILED`` and non-dependent paths keep
  running to completion; the scheduler itself never crashes.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

import networkx as nx

from odar.schemas import TaskNode, TaskStatus, TaskType

logger = logging.getLogger("odar.orchestrator")

Handler = Callable[
    [TaskNode, Dict[str, Any], Dict[str, Any]],
    Union[Any, Awaitable[Any]],
]


class DAGValidationError(Exception):
    """Raised when the task graph is structurally invalid."""


class TaskDAGOrchestrator:
    """Build, validate and execute a typed research DAG asynchronously."""

    def __init__(self) -> None:
        self.graph: nx.DiGraph = nx.DiGraph()
        self._nodes: Dict[str, TaskNode] = {}
        self._finalized = False

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def add_task(self, node: TaskNode) -> None:
        if not node.task_id:
            raise DAGValidationError("task_id must be non-empty")
        if node.task_id in self._nodes:
            raise DAGValidationError(f"duplicate task id: {node.task_id}")
        self._nodes[node.task_id] = node
        self.graph.add_node(node.task_id, task_type=node.task_type.value)
        self._finalized = False

    def _ensure_finalized(self) -> None:
        if self._finalized:
            return
        for node in self._nodes.values():
            for dependency in node.depends_on:
                if dependency not in self._nodes:
                    raise DAGValidationError(f"task '{node.task_id}' depends on unknown task '{dependency}'")
                self.graph.add_edge(dependency, node.task_id)
        if not nx.is_directed_acyclic_graph(self.graph):
            cycles = [sorted(c) for c in nx.simple_cycles(self.graph)][:3]
            raise DAGValidationError(f"cycle detected in task graph: {cycles}")
        self._finalized = True

    # ------------------------------------------------------------------ #
    # Inspection
    # ------------------------------------------------------------------ #
    def execution_order(self) -> List[str]:
        """Deterministic topological order (stable across calls)."""
        self._ensure_finalized()
        order: List[str] = []
        for generation in nx.topological_generations(self.graph):
            order.extend(sorted(generation))
        return order

    def generations(self) -> List[List[str]]:
        self._ensure_finalized()
        return [sorted(gen) for gen in nx.topological_generations(self.graph)]

    def get(self, task_id: str) -> TaskNode:
        if task_id not in self._nodes:
            raise KeyError(task_id)
        return self._nodes[task_id]

    @property
    def nodes(self) -> Dict[str, TaskNode]:
        return dict(self._nodes)

    # ------------------------------------------------------------------ #
    # Execution (async core)
    # ------------------------------------------------------------------ #
    async def run_async(
        self,
        handlers: Dict[TaskType, Handler],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, TaskNode]:
        """Execute the DAG topologically with per-generation concurrency.

        ``handlers`` maps :class:`TaskType` to ``handler(node, dep_results,
        context)`` returning a value or an awaitable.  Dependency results are
        injected as ``{parent_task_id: parent_result}`` after every parent
        has resolved.  Returns the node map with statuses and results.
        """
        self._ensure_finalized()
        context = context if context is not None else {}
        results: Dict[str, Any] = {}

        for generation in nx.topological_generations(self.graph):
            coros = [self._run_node(task_id, results, handlers, context) for task_id in sorted(generation)]
            # Independent siblings in the same generation run concurrently;
            # a quarantined failure in one never cancels the others.
            await asyncio.gather(*coros, return_exceptions=False)

        return self._nodes

    async def _run_node(
        self,
        task_id: str,
        results: Dict[str, Any],
        handlers: Dict[TaskType, Handler],
        context: Dict[str, Any],
    ) -> None:
        node = self._nodes[task_id]
        handler = handlers.get(node.task_type)
        if handler is None:
            node.status = TaskStatus.SKIPPED
            node.error = f"no handler registered for {node.task_type.value}"
            logger.warning("skipping %s: %s", task_id, node.error)
            return

        upstream_degraded = [
            dep for dep in node.depends_on if self._nodes[dep].status not in (TaskStatus.COMPLETED,)
        ]
        dep_results = {dep: results.get(dep) for dep in node.depends_on}
        node.status = TaskStatus.RUNNING
        logger.info("running %s (%s)", task_id, node.task_type.value)
        try:
            if inspect.iscoroutinefunction(handler):
                node.result = await handler(node, dep_results, context)
            else:
                node.result = await asyncio.to_thread(handler, node, dep_results, context)
            node.status = TaskStatus.COMPLETED
            results[task_id] = node.result
        except Exception as exc:  # poka-yoke: quarantine node failures
            node.status = TaskStatus.FAILED
            node.error = f"{type(exc).__name__}: {exc}"
            results[task_id] = None
            logger.error("node %s failed: %s", task_id, node.error)
        if upstream_degraded and node.status == TaskStatus.COMPLETED:
            # Surface degraded inputs without failing the child.
            node.error = f"degraded: upstream failed/skipped: {upstream_degraded}"

    # ------------------------------------------------------------------ #
    # Execution (sync facade)
    # ------------------------------------------------------------------ #
    def run(
        self,
        handlers: Dict[TaskType, Handler],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, TaskNode]:
        """Synchronous convenience wrapper around :meth:`run_async`."""
        return asyncio.run(self.run_async(handlers, context))

    # ------------------------------------------------------------------ #
    def summary(self) -> Dict[str, Any]:
        return {
            "task_count": len(self._nodes),
            "edge_count": self.graph.number_of_edges(),
            "order": self.execution_order(),
            "nodes": [self._nodes[t].to_dict() for t in self.execution_order()],
        }


# --------------------------------------------------------------------------- #
# Default research plan
# --------------------------------------------------------------------------- #
def build_research_plan(question: str) -> TaskDAGOrchestrator:
    """Decompose ``question`` into the canonical 4-stage typed DAG.

    T1 EXPLORATION  -> broad evidence survey
    T2 DATA_EXTRACTION -> quantitative fact extraction (depends: T1)
    T3 FALSIFICATION -> dialectical attack (depends: T2)
    T4 SYNTHESIS -> audited synthesis (depends: T2, T3)
    """
    orchestrator = TaskDAGOrchestrator()
    orchestrator.add_task(
        TaskNode(
            task_id="T1_exploration",
            task_type=TaskType.EXPLORATION,
            goal=f"Broadly survey authoritative evidence for: {question}",
            payload={"question": question},
        )
    )
    orchestrator.add_task(
        TaskNode(
            task_id="T2_extraction",
            task_type=TaskType.DATA_EXTRACTION,
            goal=f"Extract quantitative facts and figures for: {question}",
            depends_on=["T1_exploration"],
            payload={"question": question},
        )
    )
    orchestrator.add_task(
        TaskNode(
            task_id="T3_falsification",
            task_type=TaskType.FALSIFICATION,
            goal=f"Stress-test the leading hypothesis with counter-evidence: {question}",
            depends_on=["T2_extraction"],
            payload={"question": question},
        )
    )
    orchestrator.add_task(
        TaskNode(
            task_id="T4_synthesis",
            task_type=TaskType.SYNTHESIS,
            goal=f"Produce a citation-audited synthesis answering: {question}",
            depends_on=["T2_extraction", "T3_falsification"],
            payload={"question": question},
        )
    )
    return orchestrator
