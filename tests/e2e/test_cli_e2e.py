"""E2E suite: live CLI job lifecycle (network may be used; excluded from the
default offline command via the `e2e` marker)."""

import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def cli(*args, db):
    return subprocess.run(
        [sys.executable, "run_research.py", *args, "--db", db],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=600,
    )


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "jobs.db")


def test_run_status_list_cancel_lifecycle(db):
    run = cli(
        "run",
        "What is the Eiffel Tower made of?",
        "--idempotency-key",
        "cli-e2e-1",
        "--model",
        "scripted",
        "--max-search",
        "2",
        "--max-fetch",
        "1",
        "--timeout",
        "120",
        db=db,
    )
    assert run.returncode in (0, 3), run.stderr
    job_line = [line for line in run.stdout.splitlines() if line.startswith("job: ")]
    assert job_line, run.stdout
    job_id = job_line[0].split("job: ")[1].strip()

    status = cli("status", job_id, db=db)
    assert status.returncode == 0
    payload = json.loads(status.stdout)
    assert payload["job_id"] == job_id

    listing = cli("list", db=db)
    assert job_id in listing.stdout


def test_idempotent_duplicate_submission(db):
    args = [
        "run",
        "What is the Eiffel Tower made of?",
        "--idempotency-key",
        "cli-e2e-dup",
        "--model",
        "scripted",
        "--max-search",
        "1",
        "--max-fetch",
        "1",
        "--timeout",
        "120",
        "--db",
        db,
    ]
    first = subprocess.run(
        [sys.executable, "run_research.py", *args], cwd=REPO, capture_output=True, text=True, timeout=600
    )
    assert first.returncode in (0, 3), first.stderr
    second = subprocess.run(
        [sys.executable, "run_research.py", *args], cwd=REPO, capture_output=True, text=True, timeout=600
    )
    assert second.returncode == 0
    assert "[idempotent]" in second.stdout, second.stdout
