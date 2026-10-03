"""Hardened code execution sandbox (ODAR production).

Honest isolation statement: this sandbox provides **process-level isolation
with kernel-enforced POSIX resource limits plus a static policy gate** - it
is *not* a VM or container, and a determined in-kernel exploit is out of
scope.  Within that scope it defends against: arbitrary imports, reflection
jailbreaks, file access, fork bombs, memory/CPU exhaustion, runaway
execution, and output flooding.  Treat it as defense-in-depth, never as the
only security boundary.

Layers:

1. **Static AST policy gate** - before anything executes, the code is parsed
   and screened:
   * imports restricted to an explicit allowlist (``math``, ``statistics``,
     ``json``, ``re``, ``collections``, ``itertools``, ``decimal``,
     ``fractions``);
   * every dunder attribute access (``__class__``, ``__subclasses__``, ...)
     and dunder string constant is rejected;
   * dangerous builtins (``eval``, ``exec``, ``open``, ``compile``,
     ``__import__``, ``getattr``/``setattr``/``delattr``, introspection
     helpers) are rejected by name.
2. **OS-level POSIX resource limits** - the child installs
   ``resource.setrlimit`` caps (``RLIMIT_CPU``, ``RLIMIT_AS``,
   ``RLIMIT_FSIZE``, ``RLIMIT_NOFILE``, ``RLIMIT_CORE``, ``RLIMIT_NPROC``
   for fork-bomb containment) via ``preexec_fn`` before user code runs.
3. **Isolated subprocess runner** - ``python -I`` (isolated mode: no user
   site, no environment leakage), a fresh ephemeral working directory,
   a dedicated session/process group so timeouts kill the whole tree,
   best-effort privilege drop when running as root, hard wall-clock timeout,
   and byte-capped stdout/stderr digests.

The digest contract is poka-yoke: :meth:`ExecutionSandbox.run_python` never
raises; policy violations, syntax errors, runtime errors, rlimit kills and
timeouts all surface as structured ``ok=False`` digests the calling agent can
reason about.
"""

from __future__ import annotations

import ast
import json
import operator
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional

DEFAULT_TIMEOUT_S = 20.0
DEFAULT_CPU_SECONDS = 10
DEFAULT_MEMORY_BYTES = 512 * 1024 * 1024  # RLIMIT_AS: 512 MiB virtual
DEFAULT_FSIZE_BYTES = 1 * 1024 * 1024  # RLIMIT_FSIZE: 1 MiB files
DEFAULT_NOFILE = 64  # RLIMIT_NOFILE descriptors
DEFAULT_NPROC = 64  # RLIMIT_NPROC: fork-bomb containment
DEFAULT_CORE_BYTES = 0  # RLIMIT_CORE: no core dumps
MAX_OUTPUT_BYTES = 4096

WORKSPACE_DIR_MODE = 0o700  # owner (drop-target) only
SCRIPT_FILE_MODE = 0o600  # owner (drop-target) read/write only


def resolve_privdrop_target() -> Optional[int]:
    """Return the uid to drop to when running as root, else None."""
    if os.name != "posix" or not hasattr(os, "geteuid"):
        return None
    if os.geteuid() != 0:
        return None
    try:
        import pwd

        return pwd.getpwnam("nobody").pw_uid
    except (KeyError, ImportError):
        return None


def prepare_workspace_ownership(workspace: str, script_path: str, target_uid: Optional[int]) -> None:
    """Hand the ephemeral workspace to the drop-target user BEFORE spawn.

    Root-created tempdirs/scripts are unreadable to an unprivileged child;
    chown-ing to the drop target (and tightening modes) preserves both
    unprivileged execution and restrictive permissions.  Non-root launches
    are a no-op.  Failures are non-fatal: the worst case is that the child
    cannot read its own script and exits with an error digest - never a
    privilege escalation.
    """
    if target_uid is None:
        return
    try:
        import shutil as _shutil

        _shutil.chown(workspace, user=target_uid)
        os.chmod(workspace, WORKSPACE_DIR_MODE)
        _shutil.chown(script_path, user=target_uid)
        os.chmod(script_path, SCRIPT_FILE_MODE)
    except (PermissionError, OSError, LookupError, ValueError):
        # Defense-in-depth best effort; the child's own privilege drop and
        # the AST gate still apply.
        pass


#: Explicit import allowlist for sandboxed scripts.
ALLOWED_IMPORTS = frozenset(
    {"math", "statistics", "json", "re", "collections", "itertools", "decimal", "fractions"}
)

#: Builtins that must never be referenced by sandboxed code.
BLOCKED_BUILTINS = frozenset(
    {
        "eval",
        "exec",
        "open",
        "compile",
        "__import__",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "breakpoint",
        "input",
    }
)

#: Dunder escape tokens banned inside string constants (reflection jailbreaks).
DUNDER_ESCAPE_TOKENS = (
    "__class__",
    "__subclasses__",
    "__bases__",
    "__globals__",
    "__builtins__",
    "__import__",
    "__mro__",
    "__dict__",
    "__code__",
    "__closure__",
    "__func__",
    "__self__",
)


class SandboxError(Exception):
    """Raised by helpers that demand hard failures (e.g. bad expressions)."""


class SandboxPolicyViolation(SandboxError):
    """Raised/marked when code fails the static AST policy gate."""


# --------------------------------------------------------------------------- #
# Static AST policy gate
# --------------------------------------------------------------------------- #
def validate_sandbox_code(code: str) -> List[str]:
    """Screen ``code`` against the sandbox security policy.

    Returns a list of violation strings; an empty list means the code is
    cleared for isolated execution.  Syntax errors are reported as violations
    so callers get one uniform failure surface.
    """
    violations: List[str] = []
    if not isinstance(code, str) or not code.strip():
        return ["empty code rejected"]
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"SyntaxError: {exc.msg} (line {exc.lineno})"]
    except (ValueError, RecursionError) as exc:
        return [f"UnparseableCode: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = (alias.name or "").split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    violations.append(
                        f"import '{alias.name}' blocked: allowed modules are {sorted(ALLOWED_IMPORTS)}"
                    )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_IMPORTS:
                violations.append(
                    f"from-import '{node.module}' blocked: allowed modules are {sorted(ALLOWED_IMPORTS)}"
                )
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__") and len(node.attr) > 4:
                violations.append(f"dunder attribute access '{node.attr}' blocked")
        elif isinstance(node, ast.Name):
            if node.id in BLOCKED_BUILTINS:
                violations.append(f"builtin '{node.id}' blocked")
            elif node.id.startswith("__") and node.id.endswith("__") and len(node.id) > 4:
                violations.append(f"dunder name '{node.id}' blocked")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for token in DUNDER_ESCAPE_TOKENS:
                if token in node.value:
                    violations.append(f"string constant containing dunder escape token '{token}' blocked")
                    break
    return violations


# --------------------------------------------------------------------------- #
# POSIX rlimit installer (runs inside the child, pre-interpreter-user-code)
# --------------------------------------------------------------------------- #
def build_preexec_limits(
    cpu_seconds: int,
    memory_bytes: int,
    fsize_bytes: int,
    nofile: int,
    core_bytes: int,
    nproc: int = DEFAULT_NPROC,
    target_uid_for_drop: Optional[int] = None,
) -> Optional[Callable[[], None]]:
    """Return a ``preexec_fn`` installing POSIX rlimits (plus a privilege
    drop to ``target_uid_for_drop`` when provided), or ``None`` off-POSIX."""
    if os.name != "posix":
        return None

    def _apply_limits() -> None:  # pragma: no cover - executes in child process
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_bytes, fsize_bytes))
            resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))
            resource.setrlimit(resource.RLIMIT_CORE, (core_bytes, core_bytes))
            if hasattr(resource, "RLIMIT_NPROC"):
                resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
        except (ValueError, OSError, ImportError):
            # Poka-yoke: limits are defence-in-depth; isolation still holds
            # via `python -I`, the wall-clock timeout and the AST gate.
            pass
        # Privilege drop: if the parent is root, run the payload as the
        # unprivileged 'nobody' account.  The workspace ownership was already
        # transferred by prepare_workspace_ownership() before spawn.
        try:
            target_uid = target_uid_for_drop
            if target_uid is not None:
                import pwd

                os.setgid(pwd.getpwuid(target_uid).pw_gid)
                os.setuid(target_uid)
        except (KeyError, PermissionError, OSError):
            pass

    return _apply_limits


class ExecutionSandbox:
    """Hardened isolated Python subprocess runner."""

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_S,
        cpu_seconds: Optional[int] = DEFAULT_CPU_SECONDS,
        memory_bytes: int = DEFAULT_MEMORY_BYTES,
        fsize_bytes: int = DEFAULT_FSIZE_BYTES,
        nofile: int = DEFAULT_NOFILE,
        nproc: int = DEFAULT_NPROC,
        core_bytes: int = DEFAULT_CORE_BYTES,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
    ) -> None:
        self.timeout = float(timeout)
        self.cpu_seconds = int(cpu_seconds) if cpu_seconds is not None else None
        self.memory_bytes = int(memory_bytes)
        self.fsize_bytes = int(fsize_bytes)
        self.nofile = int(nofile)
        self.nproc = int(nproc)
        self.core_bytes = int(core_bytes)
        self.max_output_bytes = int(max_output_bytes)

    # ------------------------------------------------------------------ #
    def run_python(self, code: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Screen, execute and digest ``code``.  Always returns a digest.

        Digest keys: ``ok``, ``exit_code``, ``stdout``, ``stderr``,
        ``duration_s``, ``timed_out``, ``output_truncated``, ``policy_blocked``.
        """
        # Layer 1: static AST policy gate (never touches the child process).
        violations = validate_sandbox_code(code)
        if violations:
            return {
                "ok": False,
                "exit_code": -3,
                "stdout": "",
                "stderr": "SandboxPolicyViolation: " + "; ".join(violations[:8]),
                "duration_s": 0.0,
                "timed_out": False,
                "output_truncated": False,
                "policy_blocked": True,
            }

        effective_timeout = float(timeout) if timeout is not None else self.timeout
        started = time.perf_counter()
        workspace: Optional[str] = None
        script_path: Optional[str] = None
        drop_uid = resolve_privdrop_target()
        preexec = None
        if self.cpu_seconds is not None:
            preexec = build_preexec_limits(
                cpu_seconds=self.cpu_seconds,
                memory_bytes=self.memory_bytes,
                fsize_bytes=self.fsize_bytes,
                nofile=self.nofile,
                core_bytes=self.core_bytes,
                nproc=self.nproc,
                target_uid_for_drop=drop_uid,
            )
        try:
            # Ephemeral workspace: the payload runs in a fresh directory that
            # is destroyed afterwards; no shared filesystem surface.
            workspace = tempfile.mkdtemp(prefix="odar_sbx_ws_")
            fd, script_path = tempfile.mkstemp(prefix="run_", suffix=".py", dir=workspace)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(code)
            # If we are root and will drop privileges in the child, hand the
            # workspace to the drop target first (otherwise the child cannot
            # read its own script).
            prepare_workspace_ownership(workspace, script_path, drop_uid)
            minimal_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            posix = os.name == "posix"
            proc = subprocess.Popen(
                [sys.executable, "-I", script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                env=minimal_env,
                cwd=workspace,
                preexec_fn=preexec,
                start_new_session=posix,  # own session/group -> killable tree
            )
            try:
                stdout, stderr = proc.communicate(timeout=effective_timeout)
            except subprocess.TimeoutExpired:
                # Kill the entire process group (handles spawned children).
                if posix:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        proc.kill()
                else:
                    proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                return self._digest(
                    exit_code=-1,
                    stdout="",
                    stderr=f"Execution exceeded wall-clock timeout of {effective_timeout:.1f}s and was killed.",
                    started=started,
                    timed_out=True,
                )
            return self._digest(
                exit_code=proc.returncode if proc.returncode is not None else -1,
                stdout=stdout or "",
                stderr=stderr or "",
                started=started,
                timed_out=False,
            )
        except Exception as exc:  # poka-yoke: sandbox never escapes upward
            return self._digest(
                exit_code=-2,
                stdout="",
                stderr=f"SandboxInfrastructureError: {type(exc).__name__}: {exc}",
                started=started,
                timed_out=False,
            )
        finally:
            if workspace and os.path.isdir(workspace):
                try:
                    shutil.rmtree(workspace, ignore_errors=True)
                except OSError:
                    pass

    # ------------------------------------------------------------------ #
    def run_json(self, code: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Run a script that prints JSON; digest gains a parsed ``json`` key."""
        digest = self.run_python(code, timeout=timeout)
        digest["json"] = None
        if digest["ok"] and digest["stdout"].strip():
            try:
                digest["json"] = json.loads(digest["stdout"].strip().splitlines()[-1])
            except json.JSONDecodeError as exc:
                digest["ok"] = False
                digest["stderr"] = (digest["stderr"] + f"\nJSONParseError: {exc}").strip()
        return digest

    # ------------------------------------------------------------------ #
    def _digest(
        self,
        exit_code: int,
        stdout: str,
        stderr: str,
        started: float,
        timed_out: bool,
    ) -> Dict[str, Any]:
        duration = round(time.perf_counter() - started, 4)
        truncated = False
        if len(stdout.encode("utf-8", "replace")) > self.max_output_bytes:
            stdout = stdout.encode("utf-8", "replace")[: self.max_output_bytes].decode("utf-8", "replace")
            truncated = True
        if len(stderr.encode("utf-8", "replace")) > self.max_output_bytes:
            stderr = stderr.encode("utf-8", "replace")[: self.max_output_bytes].decode("utf-8", "replace")
            truncated = True
        return {
            "ok": (not timed_out) and exit_code == 0,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_s": duration,
            "timed_out": timed_out,
            "output_truncated": truncated,
            "policy_blocked": False,
        }


# --------------------------------------------------------------------------- #
# Whitelisted arithmetic evaluator (belt-and-braces poka-yoke for numbers)
# --------------------------------------------------------------------------- #
_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: Dict[type, Callable[[Any], Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def safe_eval_arithmetic(expression: str) -> float:
    """Evaluate a pure arithmetic expression via a whitelisted AST walk.

    Only numeric literals and ``+ - * / // % **`` with unary signs are
    permitted.  Anything else (names, calls, attributes, subscripts) raises
    :class:`SandboxError`, which makes injection through this path
    structurally impossible.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise SandboxError("empty expression")

    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise SandboxError(f"invalid arithmetic expression: {exc}") from exc

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                return float(node.value)
            raise SandboxError(f"constant of type {type(node.value).__name__} not allowed")
        if isinstance(node, ast.BinOp):
            bin_op = type(node.op)
            if bin_op not in _BINARY_OPS:
                raise SandboxError(f"operator {bin_op.__name__} not allowed")
            left = evaluate(node.left)
            right = evaluate(node.right)
            if bin_op is ast.Pow and abs(right) > 100:
                raise SandboxError("exponent magnitude capped at 100")
            return float(_BINARY_OPS[bin_op](left, right))
        if isinstance(node, ast.UnaryOp):
            unary_op = type(node.op)
            if unary_op not in _UNARY_OPS:
                raise SandboxError(f"unary operator {unary_op.__name__} not allowed")
            return float(_UNARY_OPS[unary_op](evaluate(node.operand)))
        raise SandboxError(f"expression node {type(node).__name__} not allowed")

    return evaluate(tree)
