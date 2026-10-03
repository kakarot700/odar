# ODAR Security Model

## Trust boundaries

Every boundary below is enforced in code, not convention:

| Boundary | Enforcement |
| --- | --- |
| SYSTEM POLICY (system prompt) | Only ODAR code composes it; model output never alters it |
| USER REQUEST | Parsed as the objective string only; never executed |
| AGENT STATE | Serialized through `to_checkpoint` with secret redaction |
| TOOL DEFINITIONS | Fixed vocabulary; the model proposes, the Governor disposes |
| UNTRUSTED CONTENT (web pages, snippets) | Wrapped/labeled, sanitized, injection-scanned, quarantined on hit |
| MODEL OUTPUT | Parsed as data (JSON decisions); invalid output falls back |

All retrieved content is UNTRUSTED DATA. Instructions found inside fetched
pages, titles or snippets are detected (`odar/trust.py::scan_for_injection`)
and the page is quarantined: it never becomes a source, never reaches claim
extraction, never reaches a prompt.

## Threat model and defenses

| Threat | Defense | Where |
| --- | --- | --- |
| SSRF to loopback/private/link-local/metadata/internal | DNS-resolved validation of every URL AND every redirect hop; literal IPs, IPv6, hostnames, credentials-in-URL all rejected | `odar/url_safety.py` |
| Redirect-to-internal | Manual redirect following with per-hop re-validation (max 3 hops) | `odar/retrieval.py::PageExtractor._safe_fetch` |
| Response bombs | Streaming read with 2 MB hard cap + declared Content-Length pre-check | same |
| Malicious content types | Content-type allowlist (html/text/xml/json) | same |
| Prompt injection (direct, hidden unicode, encoded, impersonation, fake system, tool-command) | 20+ pattern categories + invisible/control char stripping + quarantine | `odar/trust.py` |
| Self-support / circular citation | Agent-origin near-duplicate spans get `Relation.CIRCULAR` and cannot certify; final audit flags circular citations | `odar/citation_auditor.py`, `odar/final_audit.py` |
| Hallucinated citations | Final audit resolves every `[src_*]` marker against recorded sources; unresolved → FAIL | `odar/final_audit.py` |
| Malicious user code in sandbox | AST gate (import allowlist, no dunders, no open/exec/eval), POSIX rlimits (CPU/AS/FSIZE/NOFILE/NPROC/CORE), ephemeral workspace, `python -I`, own process group killed on timeout, best-effort privilege drop | `odar/sandbox.py` |
| Fork bombs | `RLIMIT_NPROC` cap (64) + wall-clock timeout + process-group kill | same |
| Unbounded agent loops | Governor iteration/tool budgets + hard loop cap (24) + wall-clock deadline token | `odar/budget.py`, `odar/engine.py` |
| Secret leakage | Redaction applied to telemetry events, checkpoints, jobstore payloads, model reasoning strings | `odar/trust.py::redact_secrets` |
| Blanket retries | Failure-classified retry policy; permanent/security failures never retried | `odar/retry.py` |
| Duplicate expensive jobs | Idempotency keys → deterministic job ids; finished jobs short-circuit | `odar/jobstore.py`, `run_research.py` |

## Sandbox honesty statement

The sandbox provides **process-level isolation with kernel-enforced POSIX
resource limits plus a static AST policy gate**. It is NOT a VM or
container, and a determined in-kernel exploit is out of scope. We do not
claim "kernel-level sandboxing" beyond what rlimits + process isolation
provide; the AST gate is the primary policy layer and rlimits are
defense-in-depth.

## What is explicitly OUT of scope (documented limitation)

* Multi-user service mode: no HTTP API layer, auth, per-tenant quotas or
  cross-user access control is implemented. ODAR is a single-tenant CLI /
  job-store system. Running it as a public service requires an external
  gateway that provides auth, rate limiting and isolation.
* Kernel-enforcement guarantees (seccomp/landlock/capabilities) are not
  configured; see honesty statement above.
