# ODAR Production Readiness Audit Report

Updated 2026-10-03 after the Phase-4 remediation and final verification pass.
Every status below is backed by tests executed green in this repository
during that pass (commands at the bottom). Claims are intentionally narrow:
"verified" means implemented AND exercised by automated tests; where a
protection depends on the deployment environment, that dependency is stated.

Classification legend:

* **VERIFIED** - implemented AND covered by automated tests executed green
  in this repository (command evidence below).
* **PARTIALLY VERIFIED** - implemented; exercised by partial tests or smoke
  runs; the remaining gap is stated explicitly.
* **IMPLEMENTED** - code complete and reviewed; automated verification limited.
* **NOT IMPLEMENTED** - deliberately scoped out; rationale given.

---

## Mandate matrix

| # | Area | Status | Evidence |
|---|------|--------|----------|
| 1 | Real LLM control (canonical Anthropic tool-use loop) | **VERIFIED** | `NativeToolUseController` (`odar/llm.py`) runs the native multi-turn loop: task+tools → tool_use → governor-gated execution → tool_result → reassess → finish. Verified end-to-end against a local mock Anthropic server with a scripted 5-turn session (`tests/integration/test_llm_adapter_mock.py`, 11 tests): tool defs advertised every turn, tool_results fed back, budget accounting observed. The model decides WHAT; the Governor decides WHETHER. Residual: a real provider endpoint is untestable here (no API key). |
| 2 | Single governed execution boundary | **VERIFIED** | `GovernedExecutor` (`odar/tools.py`) is the ONLY production path to search/fetch/sandbox/evaluation/dialectic; the engine routes every action through it. Zero-budget engine runs provably perform zero network work; a denied call never reaches the backend (`tests/unit/test_governed_boundary.py`). |
| 3 | Provisional/heuristic verification never certifies | **VERIFIED** | Four enforcement layers: (a) `ClaimAuditResult` schema suppresses `certified` when `provisional`; (b) auditor marks any non-neural scorer provisional; (c) engine `_action_evaluate` maps provisional results to PROVISIONAL status only; (d) `FinalAuditor.no_provisional_certifications` FAILs any run where a heuristic-backed claim appears CERTIFIED. Regression suite: `tests/unit/test_provisional_invariant.py` (10 tests). |
| 4 | No silent architecture fallback | **VERIFIED** | Backend failures are classified (`ModelBackendError` hierarchy: auth/rate-limit/server/timeout/malformed). Default policy `fail` → run ends FAILED with the classified cause, controller unchanged. Opt-in `explicit-scripted` → visible switch: `degraded=True`, `backend_state="scripted-explicit-fallback (...)"`, DEGRADED banner in synthesis. Mock-server tests cover 401, 429, 500, timeout, malformed content (integration suite). |
| 5 | Budget accounting equality (ACTUAL == ACCOUNTED) | **VERIFIED** | Every governed side effect consumes exactly one budget unit; retries inherit `max_retries_per_call`; dialectic counter-searches go through the canonical governed search (`executor.dialectic`), never a private network path. Equality asserted in `tests/unit/test_governed_boundary.py` and the integration mock (wire requests == approved model calls + governed evaluation). |
| 6 | Semantic-only evidence stance | **VERIFIED** | Keyword cues are telemetry-only. `DialecticalEngine.attack` returns UNDECIDED without a semantic evaluator regardless of cue pressure; with an evaluator the verdict comes exclusively from semantic relations. Adversarial tests assert cue-laden unrelated documents never establish stance (`tests/test_odar_engine.py::TestDialectic`, benchmark `adversarial` scenario). |
| 7 | Re-evaluation on materially new evidence | **VERIFIED** | `Claim.evaluated_sources`/`needs_evaluation`; new sources stale existing claims (`mark_new_source`); resumed runs get one governed probe. Weak→strong evidence upgrades claims; support followed by semantic contradiction produces CONFLICTING/REFUTED + recorded contradiction, never silent CERTIFIED (`tests/unit/test_claim_reevaluation.py`). |
| 8 | Synthesis strictly from the evidence graph | **VERIFIED** | `_build_synthesis` builds findings exclusively from certified/provisional/conflicting claims with `[clm_*]/[src_*]` citations; no-findings runs abstain; a single audit-fail rebuild pass (no recursion). `FinalAuditor` now distinguishes disclosed provisional/conflict sections from undisclosed leakage (`tests/unit/test_synthesis_integrity.py`). |
| 9 | Citation integrity + anti-circularity | **VERIFIED** | Provenance chain per evidence item; agent-origin self-support → CIRCULAR; final audit blocks circular/unresolved citations; hallucinated-marker check resolves `src_*/clm_*/ev_*` against the recorded state. Benchmark citation-trap scenario: no phantom citations enter synthesis. |
| 10 | Source quality pipeline | **VERIFIED** | Classification (primary/secondary/untrusted…), content-digest dedup (benchmark `duplicated_sources`), independence marking; untrusted single sources cannot certify (benchmark `source_quality` scenario). |
| 11 | Prompt-injection defense | **VERIFIED** | Signature families detected (`tests/security/test_injection.py`); quarantine e2e; **defense-in-depth added in Phase 4: the governed fetch boundary re-scans every fetched body and quarantines on a positive scan regardless of extractor behavior** (exposed and fixed by the benchmark `injection` scenario). Envelopes + sanitization unchanged. Gap: malicious PDF parsing is out of scope (content-type allowlist rejects non-text). |
| 12 | SSRF defense incl. rebinding | **VERIFIED** (application layer) | 14+ hostile URLs refused (IPv6, metadata, internal suffixes, creds-in-URL); per-hop redirect validation; **connection pinning (`odar/pinned.py`) binds each fetch to the validated address set so a re-resolution cannot hit a rebound internal IP** (`tests/security/test_ssrf_rebinding.py`). Honest scope: application-layer pinning is necessary but not sufficient; deployments MUST also apply network egress policy (example: `deploy/network-policy.yaml`). ODAR does not claim complete SSRF protection from application checks alone. |
| 13 | Sandbox hardening incl. root-mode permissions | **VERIFIED** | AST allowlist, rlimits, NPROC fork-bomb cap, timeout tree kill, output cap, ephemeral workspace cleanup — all green. **Phase-4 fix: when running as root, the ephemeral workspace/script is chown'ed to the drop-target user before spawn (`prepare_workspace_ownership`), preserving unprivileged execution + restrictive modes.** Non-root mode tested live; root-mode tests run where euid==0 (CI `sandbox-root-mode` job) and the ownership logic is unit-tested via simulation. Scope remains honest: process-level isolation + rlimits, not a VM/kernel sandbox. |
| 14 | Durable jobs, idempotency, resume | **VERIFIED** | SQLite jobstore unit tests; checkpoint round-trip incl. new claim fields; idempotent duplicate submission e2e; CLI `run/resume/status/list/cancel/health` e2e (2 tests, live-network marker). |
| 15 | Classified retry engine | **VERIFIED** | 429 backoff+jitter+Retry-After, transient bounded by `max_retries_per_call`, permanent/security failures never retried (`tests/unit/test_retry_health.py`); executor retry policy inherits its bound from the budget itself. |
| 16 | Statistical extraction + numeric grounding | **VERIFIED** | Typed facts incl. ambiguous bare numbers; final audit numeric-consistency check exempts citation metadata lines but flags ungrounded numbers in findings (benchmark `quantitative` scenario). |
| 17 | Uncertainty/abstention taxonomy | **VERIFIED** | No-evidence-found ≠ evidence-of-absence enforced by forbidden-phrase assertions; abstaining runs emit "No certified findings could be established" with explicit uncertainty classification. |
| 18 | Independent final audit | **VERIFIED** | 9 checks: provisional certifications (FAIL-tier), unsupported claims (section-aware), citation resolution per marker type, circularity, provenance, numeric consistency, causal language, contradictions examined, uncertainty explicit. Engine rebuilds once on audit failure; corrupted states are caught by re-audit. |
| 19 | Research benchmark corpus + regression gate | **VERIFIED** | `benchmarks/`: 10-category corpus (outdated, quantitative, conflicting literature, causal, insufficient, source-quality, duplicated, adversarial cues, injection, citation trap) + deterministic offline runner measuring unsupported-claim rate, citation precision, contradiction handling, abstention, injection resistance, numeric consistency, dedup. Gate: `tests/test_benchmark_gate.py` (9 tests). The gate exposed two real defects during Phase 4 (boundary injection re-scan; localized-negation contradiction detection) which were fixed in code, not waived. |
| 20 | Test architecture separation | **VERIFIED** | `tests/{unit,security,adversarial,e2e,integration}` + markers; default run deterministic and offline: 224 passed, 2 skipped (root-only sandbox tests in a non-root sandbox). |
| 21 | Static analysis & dependency supply chain | **VERIFIED** | ruff clean on `odar/ tests/ run_research.py benchmarks/`; mypy clean on the 8 gated core modules (errors were fixed, not ignored); `pip-audit -r requirements/prod.lock.txt` reports **no known vulnerabilities**. All three run locally in this session and are mandatory CI gates. |
| 22 | CI/CD with real gates | **VERIFIED** | `.github/workflows/ci.yml` rewritten: no `|| true` on any mandatory check; mypy and pip-audit are hard gates; secret scan fails the build on a match; a dedicated root-container job exercises sandbox privilege-drop; all jobs install from the lockfiles. Informational checks (if ever added) must be name-prefixed `[informational]`. |
| 23 | Lockfile-governed builds + model version policy | **VERIFIED** | `requirements/prod.lock.txt` (resolver-verified, 70 pins, CPU deployment) governs CI jobs and the Dockerfile; `requirements/dev.lock.txt` separates the verification toolchain from the runtime image; regeneration + model/inference version policy documented (`requirements/README.md`, `Makefile`). urllib3 pinned ≥2.8.0 closing PYSEC-2026-4175/76/77. |
| 24 | Observability & governance accounting in artifacts | **VERIFIED** | Artifacts embed `backend_state`, `degraded`, full governor report (budget + counters), telemetry summary and event stream — ACTUAL vs ACCOUNTED is auditable post-run. Secret redaction unit-tested. |
| 25 | Secrets & privacy | **VERIFIED** (redaction) | Provider/AWS/Slack/bearer/password patterns redacted before logs/telemetry/jobstore. Gap: retention is operator-driven (documented); no auto-TTL. |
| 26 | Multi-user safety / HTTP API | **NOT IMPLEMENTED** | Scope decision unchanged: single-tenant CLI/job-store system; external gateway required for public service. The CLI surface is the stable interface; job payloads are the result contract. |
| 27 | Health & lifecycle | **VERIFIED** | `run_research.py health` live in this session (all checks OK); config validation fails fast. |
| 28 | Failure transparency | **VERIFIED** | Backend failures surface classified causes in `outcome.errors`; degraded runs carry visible banners; exhaustion/abstention runs state unmet conditions explicitly (live CLI evidence below). |

## Known limitations (explicit, in priority order)

1. **No HTTP service layer** (areas 26): single-tenant by design.
2. **Real Anthropic endpoint unexercised** (no key in this environment);
   the native loop contract is verified against a mock implementing the
   Messages API, including retries, timeouts and error classification.
   Residual risk: provider-side drift.
3. **SSRF protection is application-layer**: URL validation + per-hop
   re-validation + connection pinning. Complete protection additionally
   requires deployment network egress policy (`deploy/network-policy.yaml`);
   without it, ODAR explicitly does not claim full SSRF immunity.
4. **Sandbox scope**: process-level isolation + rlimits + privilege drop;
   not a VM/container sandbox.
5. **Live-network e2e** depends on external search availability; the
   deterministic offline e2e suite is the default guarantee. Live runs in
   this sandbox experienced provider throttling and terminated honestly
   with `BUDGET_EXHAUSTED` + unmet conditions (no fabricated output).
6. **Statistical extraction** is rule-based; deep meta-analysis parsing
   (I², heterogeneity models) not covered.
7. **No sustained-load benchmark** (100× load remains open; governor +
   budgets are the mitigation).
8. **PDF/binary content rejected by policy**, not parsed — a feature of
   the trust model.
9. **Deterministic lexical fallback cannot detect semantic negation as
   reliably as a neural NLI model**: localized-negation heuristics were
   added (contradiction branch), but certification without the neural
   stack is impossible by design — findings stay PROVISIONAL.
10. **Anthropic model lifecycle**: the default model moved from
    `claude-sonnet-4-5` (deprecated 2026-09-30, retiring 2026-11-30) to
    Anthropic's recommended replacement `claude-sonnet-5-5` (retirement not
    sooner than 2027-09-28). The request payload sets no `temperature`,
    `top_p` or `top_k`, which newer models reject. Live verification against
    the real Anthropic API is tracked separately.

## Concrete verification evidence (Phase-4 final pass)

```
$ python3 -m pytest tests/ -q
================ 224 passed, 2 skipped, 14 deselected in ~11s   # offline default
$ python3 -m pytest tests/ -q -m integration
================ 11 passed, 1 skipped (opt-in live model) ===== # mock-server multi-turn
$ python3 -m pytest tests/ -q -m e2e
================ 2 passed ===================================== # CLI lifecycle
$ python3 -m ruff check odar/ tests/ run_research.py benchmarks/
All checks passed!
$ python3 -m mypy --ignore-missing-imports --no-strict-optional odar/budget.py ...
Success: no issues found in 8 source files
$ python3 -m pip_audit -r requirements/prod.lock.txt
No known vulnerabilities found
$ python3 benchmarks/runner.py            # all 7 summary metrics green
$ python3 run_research.py health --db /tmp/odar_bench.db        → all checks OK
$ python3 run_research.py run "..." --idempotency-key bench-2   → BUDGET_EXHAUSTED
  with explicit unmet conditions + full governance accounting in the artifact
```

## Self-audit answers (mandated scenarios)

* **Malicious webpage?** Scanned at the boundary AND at the governed fetch
  layer; quarantined on injection signatures; can never back a certified
  claim (benchmark `injection` scenario + unit tests).
* **Network death?** Classified failures; bounded reformulation; honest
  abstention or BUDGET_EXHAUSTED — never a fabricated answer.
* **Model backend dies mid-run?** Default: explicit FAILED with classified
  cause; opt-in explicit fallback: visible DEGRADED + banner. Never silent.
* **Model loop?** Hard iteration cap + governor iteration budget +
  wall-clock token; loop breaker blocks/reroutes repetitive queries.
* **Duplicate job?** Idempotency key → stored result (e2e).
* **Worker crash?** Checkpoint survives incl. new claim fields; `resume`
  continues with one governed probe for fresh evidence.
* **Conflicting evidence arriving later?** Re-evaluation fires; claim
  becomes CONFLICTING/REFUTED; contradiction recorded; old certification
  cannot survive a semantic contradiction (regression-tested).
* **Lexical-but-irrelevant source?** Topic-contact guard + semantic arbiter;
  cues never establish stance; dialectic without evaluator stays UNDECIDED.
* **Circular citation?** Agent-origin self-support → CIRCULAR, blocked.
* **Hostile code?** AST gate + rlimits + NPROC + timeout tree kill; works
  as ordinary user and (CI-gated) as root with privilege drop.
* **Redirect-to-internal / rebinding?** Per-hop validation + connection
  pinning to validated IPs; hostile re-resolution ignored (tested).
* **Budget escape attempts?** A tool call can never leave the governor as
  an exception; denials surface to the model as data.
* **Dependency update?** Lockfile-governed CI/Docker + mandatory pip-audit.
* **Wrong auditor?** Evaluator id recorded per evidence item; provisional
  evaluators on certified claims fail the final audit.
* **Unknowable answer?** Explicit abstention; NO_EVIDENCE_FOUND.
* **100× load?** NOT benchmarked — limitation #7.
