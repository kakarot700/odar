.PHONY: lock test test-integration typecheck audit

lock:  ## Regenerate lockfiles (see requirements/README.md for the procedure)
	@echo "Follow requirements/README.md - lockfiles are hand-verified pins."

test:
	python3 -m pytest tests/ -q

test-integration:
	python3 -m pytest tests/ -q -m integration

typecheck:
	python3 -m mypy --ignore-missing-imports --no-strict-optional \
		odar/budget.py odar/trust.py odar/url_safety.py odar/retry.py \
		odar/evidence.py odar/jobstore.py odar/telemetry.py odar/health.py

audit:
	python3 -m pip_audit -r requirements/prod.lock.txt
