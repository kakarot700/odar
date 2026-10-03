# Contributing

Contributions should preserve ODAR's governed execution boundary, evidence provenance, security controls, and documented limitations. Keep changes focused and include tests for behavior changes; do not weaken a test or security gate merely to obtain a passing run.

## Development setup

Use Python 3.13 to match the checked-in lockfiles and CI. Follow the locked development setup in the [README](README.md) and [dependency-lock guide](requirements/README.md). Do not commit credentials, local databases, generated research output, model caches, or machine-specific files.

## Validation

Before opening a pull request, run the relevant checks:

```bash
python3 -m ruff check odar/ tests/ run_research.py benchmarks/
python3 -m ruff format --check odar/ tests/ run_research.py benchmarks/
python3 -m mypy --ignore-missing-imports --no-strict-optional \
  odar/budget.py odar/trust.py odar/url_safety.py odar/retry.py \
  odar/evidence.py odar/jobstore.py odar/telemetry.py odar/health.py
python3 -m pytest tests/ -q
python3 benchmarks/runner.py
```

Integration and live-network E2E tests are opt-in; see the README before running them. CI is authoritative for the repository's required checks.

## Security reports

Do not file public issues containing credentials or exploitable details. Review [`docs/SECURITY.md`](docs/SECURITY.md) and use GitHub's private vulnerability-reporting mechanism if it is enabled for the repository. Otherwise, contact the repository owner privately.
