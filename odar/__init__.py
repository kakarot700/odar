"""ODAR governed agentic research engine.

The production research CLI uses a native Anthropic tool-use controller or a
deterministic offline controller. Both route side effects through the governed
execution boundary, with explicit evidence auditing and provenance.

The package also retains DAG orchestration and agent-loop utilities used by
the compatibility API and tests. See the documentation for security scope and
known limitations; the sandbox provides process-level isolation, not a VM.
"""

from odar.agent import (
    AgentLoop,
    AnthropicSDKAdapter,
    LocalPolicyModel,
    ToolDispatcher,
    TOOL_SPECS,
    build_anthropic_request,
)
from odar.citation_auditor import CitationAuditor, DeterministicNLIScorer
from odar.dialectic import DialecticalEngine, DialecticReport
from odar.loop_breaker import (
    ALTERNATE_VECTORS,
    LoopBreakerDecision,
    SemanticLoopBreaker,
    cosine_similarity,
    jaccard_similarity,
)
from odar.orchestrator import (
    DAGValidationError,
    TaskDAGOrchestrator,
    build_research_plan,
)
from odar.retrieval import PageExtractor, ZeroCostSearch, build_pooled_session
from odar.sandbox import (
    ALLOWED_IMPORTS,
    BLOCKED_BUILTINS,
    ExecutionSandbox,
    SandboxError,
    SandboxPolicyViolation,
    safe_eval_arithmetic,
    validate_sandbox_code,
)
from odar.schemas import (
    AgentTrace,
    ClaimAuditResult,
    ExtractedPage,
    ModelEvent,
    SearchHit,
    SelfCorrectionTrace,
    StopReason,
    TaskNode,
    TaskStatus,
    TaskType,
    ToolSpec,
    Verdict,
    validate_payload,
)

__version__ = "3.0.0"

__all__ = [
    "AgentLoop",
    "AgentTrace",
    "AnthropicSDKAdapter",
    "ALLOWED_IMPORTS",
    "ALTERNATE_VECTORS",
    "BLOCKED_BUILTINS",
    "CitationAuditor",
    "ClaimAuditResult",
    "DAGValidationError",
    "DeterministicNLIScorer",
    "DialecticReport",
    "DialecticalEngine",
    "ExecutionSandbox",
    "ExtractedPage",
    "LocalPolicyModel",
    "LoopBreakerDecision",
    "ModelEvent",
    "PageExtractor",
    "SandboxError",
    "SandboxPolicyViolation",
    "SearchHit",
    "SelfCorrectionTrace",
    "SemanticLoopBreaker",
    "StopReason",
    "TOOL_SPECS",
    "TaskDAGOrchestrator",
    "TaskNode",
    "TaskStatus",
    "TaskType",
    "ToolDispatcher",
    "ToolSpec",
    "Verdict",
    "ZeroCostSearch",
    "build_anthropic_request",
    "build_pooled_session",
    "build_research_plan",
    "cosine_similarity",
    "jaccard_similarity",
    "safe_eval_arithmetic",
    "validate_payload",
    "validate_sandbox_code",
    "__version__",
]
