"""SECURITY suite: hardened sandbox behavior (offline, deterministic)."""

import os

from odar.sandbox import ExecutionSandbox


def make_sandbox(timeout=12.0):
    return ExecutionSandbox(timeout=timeout)


def test_fork_bomb_contained():
    sandbox = make_sandbox()
    digest = sandbox.run_python("import os\nwhile True:\n    os.fork()\n")
    assert digest["ok"] is False
    # Must fail via policy (os blocked by AST gate) or rlimit - never succeed.


def test_open_blocked_by_ast_gate():
    digest = make_sandbox().run_python("open('/etc/passwd').read()")
    assert digest["ok"] is False
    assert digest["policy_blocked"] is True
    assert "blocked" in digest["stderr"].lower()


def test_dunder_reflection_blocked():
    digest = make_sandbox().run_python("().__class__.__bases__[0].__subclasses__()")
    assert digest["ok"] is False


def test_import_allowlist_enforced():
    digest = make_sandbox().run_python("import subprocess\nsubprocess.run(['ls'])")
    assert digest["ok"] is False


def test_allowed_imports_still_work():
    digest = make_sandbox().run_python("import math\nprint(math.isqrt(16))")
    assert digest["ok"] is True and digest["stdout"].strip() == "4"


def test_wall_clock_timeout_kills_tree():
    sandbox = ExecutionSandbox(timeout=2.0)
    digest = sandbox.run_python("import itertools\nwhile True:\n    pass\n")
    assert digest["ok"] is False
    assert digest.get("timed_out") is True


def test_output_cap_enforced():
    digest = make_sandbox().run_python("print('A' * 100000)")
    assert digest["ok"] is True
    assert len(digest["stdout"]) <= 4096 + 64


def test_giant_input_handled():
    code = "x = 1\n" * 30000 + "print('done')"
    digest = make_sandbox(timeout=15.0).run_python(code)
    assert digest["ok"] is True and digest["stdout"].strip() == "done"


def test_ephemeral_workspace_leaves_no_residue():
    import glob
    import tempfile

    pattern = os.path.join(tempfile.gettempdir(), "odar_sbx_ws_*")
    before = set(glob.glob(pattern))
    digest = make_sandbox().run_python("print('hi')")
    assert digest["ok"] is True
    after = set(glob.glob(pattern))
    assert after == before  # the ephemeral workspace was destroyed


def test_no_core_dumps_configured():
    from odar.sandbox import DEFAULT_CORE_BYTES

    assert DEFAULT_CORE_BYTES == 0
