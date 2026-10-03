"""REGRESSION (P0 FIX 7): sandbox privilege/directory permissions.

The sandbox must work when launched as an ordinary user AND when launched
as root (e.g. inside a container), where the child drops to an
unprivileged account: the ephemeral workspace must be readable by the drop
target, never left as a root-owned unreadable trap.
"""

import os
import stat
import tempfile

import pytest

from odar.sandbox import (
    SCRIPT_FILE_MODE,
    WORKSPACE_DIR_MODE,
    ExecutionSandbox,
    prepare_workspace_ownership,
    resolve_privdrop_target,
)


class TestOrdinaryUserMode:
    def test_basic_execution(self):
        digest = ExecutionSandbox().run_python("print(6*7)")
        assert digest["ok"] is True and digest["stdout"].strip() == "42"

    def test_allowed_imports(self):
        digest = ExecutionSandbox().run_python("import math, statistics, json, re\nprint(math.gcd(8, 12))")
        assert digest["ok"] is True and digest["stdout"].strip() == "4"

    def test_blocked_imports(self):
        for code in ("import subprocess", "import os", "import socket", "import sys"):
            digest = ExecutionSandbox().run_python(code)
            assert digest["ok"] is False, code

    def test_dunder_access_blocked(self):
        digest = ExecutionSandbox().run_python("x = ().__class__")
        assert digest["ok"] is False and digest["policy_blocked"] is True

    def test_output_cap(self):
        digest = ExecutionSandbox().run_python("print('B' * 50000)")
        assert digest["ok"] is True
        assert digest["output_truncated"] is True
        assert len(digest["stdout"]) <= 4096 + 64

    def test_timeout_and_process_tree_cleanup(self):
        digest = ExecutionSandbox(timeout=2.0).run_python("while True:\n    pass\n")
        assert digest["ok"] is False and digest["timed_out"] is True

    def test_giant_input(self):
        code = "y = 1\n" * 40000 + "print('giant-ok')"
        digest = ExecutionSandbox(timeout=15.0).run_python(code)
        assert digest["ok"] is True and digest["stdout"].strip() == "giant-ok"

    def test_workspace_cleanup(self):
        import glob

        pattern = os.path.join(tempfile.gettempdir(), "odar_sbx_ws_*")
        before = set(glob.glob(pattern))
        ExecutionSandbox().run_python("print('x')")
        after = set(glob.glob(pattern))
        assert after == before

    def test_fork_abuse_contained(self):
        digest = ExecutionSandbox().run_python("import os\nwhile True:\n    os.fork()\n")
        assert digest["ok"] is False  # AST gate (os blocked) or RLIMIT_NPROC


class TestOwnershipPreparation:
    """Unit coverage of the root-mode fix, simulated on any platform."""

    def test_noop_for_non_root(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        script = workspace / "run_x.py"
        script.write_text("print(1)")
        before_mode = stat.S_IMODE(os.stat(workspace).st_mode)
        prepare_workspace_ownership(str(workspace), str(script), None)
        assert stat.S_IMODE(os.stat(workspace).st_mode) == before_mode

    def test_root_mode_transfers_ownership_and_restricts(self, tmp_path, monkeypatch):
        chowned = []
        chmods = []

        class FakeShutil:
            @staticmethod
            def chown(path, user=None, group=None):
                chowned.append((path, user))

        workspace = tmp_path / "ws"
        workspace.mkdir()
        os.chmod(workspace, 0o755)
        script = workspace / "run_x.py"
        script.write_text("print(1)")
        os.chmod(script, 0o644)

        import shutil as real_shutil

        monkeypatch.setattr(real_shutil, "chown", FakeShutil.chown, raising=False)
        monkeypatch.setattr(os, "chmod", lambda path, mode: chmods.append((path, mode)))

        prepare_workspace_ownership(str(workspace), str(script), 65534)
        assert (str(workspace), 65534) in chowned
        assert (str(script), 65534) in chowned
        assert (str(workspace), WORKSPACE_DIR_MODE) in chmods
        assert (str(script), SCRIPT_FILE_MODE) in chmods

    def test_failures_are_non_fatal(self, tmp_path, monkeypatch):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        script = workspace / "run_x.py"
        script.write_text("print(1)")

        def boom(*args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(os, "chmod", boom)
        # Must not raise - a failed chown/chmod degrades, never crashes.
        prepare_workspace_ownership(str(workspace), str(script), 65534)


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0,
    reason="requires root (container/test environment)",
)
class TestRootExecutionMode:
    def test_root_run_still_executes_and_drops(self):
        # As root, the workspace is chown'ed to nobody before spawn and the
        # child runs unprivileged; execution must still succeed.
        digest = ExecutionSandbox().run_python("import os\nprint(os.getuid())")
        # 'os' is blocked by the AST gate even as root.
        assert digest["ok"] is False and digest["policy_blocked"] is True
        # Plain execution must still work after the privilege drop.
        digest2 = ExecutionSandbox().run_python("print('root-mode-ok')")
        assert digest2["ok"] is True and digest2["stdout"].strip() == "root-mode-ok"

    def test_root_resolves_drop_target(self):
        assert resolve_privdrop_target() is not None
