# Dependency lockfiles

Two lockfiles govern every build:

| File | Used by | Contents |
|---|---|---|
| `prod.lock.txt` | CI test jobs, container image | Pinned production runtime deps only |
| `dev.lock.txt`  | CI static/security jobs | prod + pinned test/lint/audit toolchain |

Dev tooling is deliberately kept OUT of the production runtime set so the
container image never ships pytest/ruff/mypy/pip-audit.

## Regeneration procedure

```bash
# 1. Fresh virtualenv, supported Python (3.13)
python3 -m venv .lockenv && . .lockenv/bin/activate

# 2. CPU torch first (free wheel), then the project with dev extras
python3 -m pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu
python3 -m pip install -e ".[dev]"

# 3. Resolve into the lockfiles (pin what the suite was verified against)
python3 -m pip freeze > /tmp/resolved.txt
#   update prod.lock.txt / dev.lock.txt pins from /tmp/resolved.txt

# 4. MANDATORY: the full suite must pass against the new pins
python3 -m pytest tests/ -q
python3 -m pytest tests/ -q -m integration
python3 -m mypy --ignore-missing-imports --no-strict-optional odar/budget.py odar/trust.py \
    odar/url_safety.py odar/retry.py odar/evidence.py odar/jobstore.py odar/telemetry.py odar/health.py
python3 -m pip_audit -r requirements/prod.lock.txt
```

Only commit the lockfiles when every step above is green.

## Model / inference version policy

- The NLI verifier model is pinned through `sentence-transformers` +
  `transformers` in `prod.lock.txt`; the model artifact itself
  (`cross-encoder/nli-deberta-v3-small`) is addressed by name in
  `odar/citation_auditor.py`.  Bumping the model REQUIRES re-running the
  adversarial + certification suites because entailment calibration changes.
- When no neural stack is available, ODAR degrades to the deterministic
  lexical scorer and **cannot certify** - this is enforced by
  `tests/unit/test_provisional_invariant.py` and is not configurable.
- The Anthropic model name used for the production tool loop is
  `odar.agent.DEFAULT_ANTHROPIC_MODEL`; changing it requires re-running the
  integration suite (`tests/integration/`).
