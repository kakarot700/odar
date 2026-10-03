# ODAR Architecture

> Describes the implementation as it exists in this repository. Nothing in
> this document is aspirational; every component named here has code and
> tests.

## Governing principle

**The LLM (or offline controller) decides WHAT to research next.
The Governor decides whether it is PERMITTED and whether evidence is VALID.**

The model never touches the network, the sandbox, budgets, URL policy,
certification thresholds or termination conditions directly. Every action
passes an approval gate in `odar/budget.py::Governor` before execution.

```
            ┌─────────────────────────────────────────────────────┐
            │                  ResearchEngine                     │
            │                                                     │
 objective─▶│  ResearchState ──▶ Model.decide() ──▶ decision      │
            │       ▲                                  │          │
            │       │                          Governor approval   │
            │       │                          (budget/security)   │
            │       │                                  ▼          │
            │       └── state update ◀── execute action           │
            │                       (search/fetch/extract/        │
            │                        evaluate/dialectic/           │
            │                        synthesize/finish)            │
            │                                                     │
            │  checkpoint per action ──▶ JobStore (SQLite)        │
            └─────────────────────────────────────────────────────┘
                                   │
                                   ▼
                        FinalAuditor (independent)
```

## Component map

| Module | Responsibility |
| --- | --- |
| `odar/engine.py` | Goal-driven loop, action dispatch, termination-condition matrix |
| `odar/research_state.py` | Explicit research state + checkpoint serialization/resume |
| `odar/llm.py` | `NativeToolUseController` (production Anthropic tool-use loop) and `ScriptedResearchController` (deterministic offline controller) |
| `odar/budget.py` | Budgets, Governor approval gates, adaptive extensions, cancellation token with wall-clock deadline |
| `odar/trust.py` | Unicode/control sanitization, injection scanner, untrusted-content envelopes, secret redaction |
| `odar/url_safety.py` | SSRF validation (DNS-resolved), redirect-chain validation, fetch policy constants |
| `odar/retry.py` | Failure-classified retry policy (429 backoff+jitter, 5xx bounded, permanent never) |
| `odar/retrieval.py` | 3-tier zero-cost search cascade + SSRF-safe PageExtractor with injection scan |
| `odar/evidence.py` | Relation/Uncertainty taxonomy, provenance records, circularity detection |
| `odar/source_quality.py` | Publisher classification, quality weights, dedup + independence |
| `odar/stats_extraction.py` | Typed statistical fact extraction with ambiguity flags |
| `odar/evidence_eval.py` | Semantic (NLI) stance evaluation - never lexical cues alone |
| `odar/citation_auditor.py` | NLI certification, provenance-aware audit, anti-self-support |
| `odar/dialectic.py` | Adversarial counter-search with topic-contact guard |
| `odar/final_audit.py` | Independent post-synthesis audit (writer never judges itself) |
| `odar/sandbox.py` | AST gate + POSIX rlimits + ephemeral workspace + process-tree kill |
| `odar/jobstore.py` | Durable jobs: idempotency, lease, checkpoint, cancel |
| `odar/telemetry.py` | Redacted structured events, counters, latencies |
| `odar/health.py` | Liveness / readiness / config validation |
| `odar/orchestrator.py` | Async DAG execution (utility; preserved from v2) |
| `odar/agent.py` | Official Anthropic SDK adapter used by the native controller; legacy tool-loop and DAG utilities remain available |

## The research loop (not a pipeline)

The loop is state-reactive. Each iteration:

1. Governor checks iteration budget + cancellation (token / job-store poll).
2. The model reads a digest of `ResearchState` + budget report and proposes
   one action from a fixed vocabulary: `search`, `fetch`, `extract_claims`,
   `evaluate_evidence`, `dialectic_attack`, `synthesize`, `finish`,
   `request_extension`.
3. The engine validates and gates the action (search budget, loop breaker,
   fetch budget + SSRF policy, model-call budget...).
4. The action executes; the state is updated; a checkpoint is persisted.
5. Termination conditions are re-evaluated.

The offline controller (`ScriptedResearchController`) embodies adaptive
research behavior deterministically: open with a compact query; fetch ranked
hits before reformulating; reformulate with distinct probes when searches
fail; extract claims once sources exist; evaluate evidence semantically;
attack open contradictions before concluding; synthesize only certified
claims; abstain (`NO_EVIDENCE_FOUND`) rather than fabricate when retrieval
yields nothing.

## Termination conditions

A run may report `COMPLETE` only when ALL hold; otherwise the unmet list is
recorded and surfaced (failure transparency):

1. Objective addressed (claims or explicit abstention exist).
2. Claims have semantic (NLI) evidence.
3. Contradictions examined.
4. Provenance complete for certified claims (URL, resolved URL, timestamp,
   HTTP status, title, publisher class, content hash, span hash, evaluator).
5. No security-policy violations (quarantined pages excluded; denials logged).
6. Final audit passed.
7. Uncertainty explicit.
8. Budgets respected (else status `BUDGET_EXHAUSTED`).

## Evidence model

Relations: `SUPPORTS`, `REFUTES`, `QUALIFIES`, `IRRELEVANT`,
`INSUFFICIENT`, `CIRCULAR`.

* SUPPORTS/REFUTES/QUALIFIES come from the NLI scorer (entailment vs
  contradiction probabilities), never from keyword cues.
* CIRCULAR is reserved for **agent-origin self-support**: ODAR-generated
  content citing itself. Direct quotation of an external source is a
  legitimate citation (flagged via `circularity_score` for transparency).
* Uncertainty taxonomy keeps `NO_EVIDENCE_FOUND` and
  `INSUFFICIENT_EVIDENCE` distinct from `EVIDENCE_AGAINST`. The system
  abstains; it never converts absence of evidence into evidence of absence.

## Model backends

* **Production**: `ANTHROPIC_API_KEY` present and `--model llm` selected →
  `NativeToolUseController` (`odar/llm.py`) uses the Anthropic Messages API
  through `AnthropicSDKAdapter` (`odar/agent.py`). The controller contract is
  tested against a local mock server; a real provider endpoint is not verified
  by those tests.
* **Offline**: without a key, the CLI defaults to
  `ScriptedResearchController`; no Anthropic request is made.
* Provider/backend failures fail the run by default with a classified cause.
  `--llm-fallback explicit-scripted` opts into a visible, degraded switch to
  the scripted controller.
* `LocalPolicyModel` remains in `odar/agent.py` for the legacy agent/DAG
  utilities and is not the production research CLI controller.

## Durability

SQLite job store (`odar/jobstore.py`): jobs table + events table.
Idempotency keys map deterministically to job ids
(`sha256(key)[:24]`); duplicate submissions return the existing job.
Leasing (`PENDING → RUNNING`) prevents double execution. Checkpoints
serialize the full research state after every action; `resume` rebuilds the
state and continues. Cancellation is a durable flag polled by the running
loop.
