# ODAR Operations Guide

## Installation

```bash
# Match the lockfile and CI Python version (3.13).
python3 -m pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu

# Production runtime
python3 -m pip install -r requirements/prod.lock.txt
python3 -m pip install -e . --no-deps

# For development and tests, use the development lock instead of prod.lock.txt:
# python3 -m pip install -r requirements/dev.lock.txt
# python3 -m pip install -e . --no-deps
```

The package metadata declares Python 3.10+, but the checked-in locks and CI are verified on Python 3.13; other Python versions are not covered by the current locked verification.

## Running research jobs

```bash
# One-shot run (scripted offline controller without ANTHROPIC_API_KEY,
# real LLM controller when the key is set)
python3 run_research.py run "What is the evidence for X?" \
    --idempotency-key my-study-1 --timeout 300

# Pin the backend explicitly
python3 run_research.py run "..." --model scripted
python3 run_research.py run "..." --model llm [--base-url URL]

# Budget control
python3 run_research.py run "..." --max-iterations 12 --max-search 6 \
    --max-fetch 4 --timeout 300 --fast   # --fast halves budgets
```

Outputs per job: `odar_output/<job_id>.md` (report) and
`odar_output/<job_id>.json` (full payload: digest, evidence, audit,
telemetry, unmet conditions).

## Job lifecycle

```bash
python3 run_research.py list --limit 20
python3 run_research.py status <job_id> [--events]
python3 run_research.py cancel <job_id>     # running loop polls and stops
python3 run_research.py resume <job_id>     # continue from last checkpoint
python3 run_research.py health              # liveness/readiness report
```

Crash recovery: a worker crash leaves the job RUNNING with its checkpoint
intact; `resume` re-leases and continues from the checkpoint. Interrupts
(Ctrl-C) requeue the job automatically.

## Idempotency

Submit with `--idempotency-key`. The job id is derived deterministically
(`sha256(key)`); a repeat submission returns the stored result instead of
running again. Verified live in this repository's test evidence.

## Configuration

Environment:

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Enables the real-LLM controller |
| `ODAR_ANTHROPIC_BASE_URL` | Alternate (e.g. verified local) API endpoint |
| `ODAR_DB` | Default job-store path (default `odar_jobs.db`) |

CLI flags override environment. Config validation runs at `run` startup and
fails fast on invalid budgets/model selection.

## Deployment

* **Container**: `docker build -t odar-engine .` - runs as non-root uid
  10001, volumes at `/var/lib/odar` (job store) and `/opt/odar/odar_output`.
* **CI**: `.github/workflows/ci.yml` - static analysis & secret scan,
  offline deterministic suite, integration (mock LLM server + live NLI
  model download), e2e CLI lifecycle, image build, health smoke.
* **Promotion/rollback**: images are immutable tags; rollback = redeploy
  the previous tag. The SQLite job store is schema-forward-compatible
  (additive columns only); take a file-level backup before upgrades.

## Observability

Every run produces (in the JSON payload and `status --events`):

* counters: iterations, search/fetch/model calls, denials, loop-breaker
  blocks/reroutes, injection quarantines, certifications, refutations,
  contradictions, audit failures, cancellations;
* latencies (p50/p95/max) per tool;
* redacted event stream with run correlation ids (`run_id`, `src_*`,
  `clm_*`, `ev_*`);
* unmet termination conditions - runs never end silently "polished".

## Failure behavior (what operators will see)

* Network dead / throttled: searches fail or return empty; the controller
  reformulates a bounded number of times, then the run terminates
  `BUDGET_EXHAUSTED` or completes with explicit abstention
  (`NO_EVIDENCE_FOUND`). No fabricated findings, ever.
* Provider failure: the default policy fails the run with a classified cause.
  Operators may explicitly select `--llm-fallback explicit-scripted`; that
  switch is visible in the result as a degraded scripted run.
* Injection detected: page quarantined, counted in telemetry, excluded
  from all downstream use; the run continues with remaining sources.
* Audit failure: implicated claims are demoted; if no certified claims
  survive, the run abstains and says so.

## Retention

Job rows persist in SQLite until removed. To purge:
`sqlite3 $ODAR_DB "DELETE FROM jobs; DELETE FROM job_events; VACUUM;"`
(operators own retention policy; ODAR records no PII beyond the submitted
objective text, which is secret-redacted before storage).
