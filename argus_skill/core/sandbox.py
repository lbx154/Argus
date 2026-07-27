"""Codex sandbox policy for builder roles (engineer / reviewer / planner / subagent).

Domain-agnostic plumbing: decides whether a codex-spawning role runs sandboxed
(``-s workspace-write`` confined to its project workdir) instead of the legacy
``--dangerously-bypass-approvals-and-sandbox`` (no sandbox at all), and computes
the ``--add-dir`` writable allowlist.

OFF by default. Opt in with ``ARGUS_SKILL_ENGINEER_SANDBOX=workspace-write`` once
the sandbox is verified on the box (network, ~/.cache, kube/B200 access all
working). The default keeps existing 7x24 daemons byte-for-byte unchanged.

Containment invariant: a sandboxed builder may write ONLY its project workdir
(the codex ``-C`` root) plus the minimal out-of-cwd allowlist below; it must
NEVER be able to write the "gate's brain" (``~/.argus-skill``: special_prompts /
skills / capabilities / per-project checkpoint+events), the package source, or
``~/.codex`` — because writes there let the engineer edit its own gate / poison
the reviewer without touching the package.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SANDBOX_ENV = "ARGUS_SKILL_ENGINEER_SANDBOX"
# Only genuinely-containing modes are accepted. ``danger-full-access`` is
# deliberately NOT here: it leaves ~/.argus-skill / the package / ~/.codex fully
# writable, so accepting it would let a run *present* as "sandboxed" while the
# self-gate-rewrite hole stays wide open. An unknown/dangerous value resolves to
# ``None`` (the honest legacy bypass), never a fake sandbox.
_VALID_MODES = {"workspace-write", "read-only"}


def engineer_sandbox_mode() -> str | None:
    """Resolve the codex sandbox mode for builder roles, or ``None`` for the
    legacy ``dangerous_yolo`` path. Default OFF (``None``)."""
    val = os.environ.get(_SANDBOX_ENV, "").strip().lower()
    if val in _VALID_MODES:
        return val
    if val in {"1", "true", "yes", "on"}:
        return "workspace-write"
    return None


def _package_root() -> str | None:
    try:
        import argus_skill

        return str(Path(argus_skill.__file__).resolve().parent.parent)
    except Exception:  # pragma: no cover — defensive
        return None


def forbidden_write_roots(*, life_root: str | os.PathLike[str] | None = None) -> list[str]:
    """Paths a sandboxed builder must NEVER be able to write."""
    from .paths import global_root

    home = Path.home()
    # ~/.argus-skill = the gate's brain; ~/.codex = codex config; the package
    # source = the harness itself; ``sys.prefix`` = the active venv, whose
    # site-packages auto-runs ``.pth`` / ``sitecustomize.py`` at the start of
    # EVERY python process — so a write there escapes the sandbox into the next
    # un-sandboxed interpreter (the daemon worker, the gate-check subprocess,
    # the reviewer/planner). On a non-editable install ``sys.prefix`` also *is*
    # where the package source lives.
    roots = [
        str(global_root()),
        str(home / ".argus-skill"),
        str(home / ".codex"),
        str(Path(sys.prefix)),
    ]
    pkg = _package_root()
    if pkg:
        roots.append(pkg)
    if life_root:
        roots.append(str(Path(life_root)))
    # de-dup, normalized
    out: list[str] = []
    for r in roots:
        rp = str(Path(r))
        if rp not in out:
            out.append(rp)
    return out


def _is_forbidden(path: str, forbidden: list[str]) -> bool:
    p = str(Path(path))
    for f in forbidden:
        if p == f or p.startswith(f.rstrip("/") + os.sep):
            return True
    return False


def fail_closed_workdir() -> str:
    """A contained write-root for a sandboxed spawn that has NO project workdir.

    Without ``-C``, codex ``workspace-write`` roots its writable workspace at the
    inherited cwd — and the 7x24 daemon runs at ``/`` (``os.chdir("/")``), which
    would hand a sandboxed role write access to the WHOLE filesystem (incl. the
    gate brain and the package source). So whenever no workdir is supplied we
    fall closed to a private, per-process scratch dir under the temp root instead
    of ever emitting a rootless ``-s workspace-write``.

    The pid-derived path is predictable and ``/tmp`` is in the sandboxed
    engineer's ``--add-dir`` allowlist, so a prior turn can PRE-PLANT it as a
    symlink to the gate brain (``mkdir(exist_ok=True)`` follows the link, handing
    the rootless reviewer's ``-C`` the real inode). If the scratch is a symlink or
    resolves under a forbidden root we abandon it for a fresh, unpredictable,
    non-symlink dir — mirroring ``writable_roots``' realpath hardening for this
    ``-C`` path."""
    d = Path(tempfile.gettempdir()) / f"argus-sandbox-scratch-{os.getpid()}"
    try:
        d.mkdir(parents=True, exist_ok=True)
        if not d.is_symlink():
            real = os.path.realpath(d)
            forbidden = [os.path.realpath(f) for f in forbidden_write_roots()]
            if not _is_forbidden(real, forbidden):
                return real
    except Exception:  # pragma: no cover — defensive
        pass
    # Pre-planted symlink / forbidden target, or mkdir failed: a fresh,
    # unpredictable, non-symlink scratch that could not have been pre-planted.
    try:
        return tempfile.mkdtemp(prefix=f"argus-sandbox-scratch-{os.getpid()}-")
    except Exception:  # pragma: no cover — defensive
        return tempfile.gettempdir()


def writable_roots(*, life_root: str | os.PathLike[str] | None = None) -> list[str]:
    """``--add-dir`` allowlist for a sandboxed builder: the minimal set of
    out-of-cwd dirs autonomous research legitimately writes. The project workdir
    is the ``-C`` root (always writable) and is NOT included here. Any candidate
    under a forbidden root is dropped."""
    home = Path.home()
    candidates = [
        str(home / ".cache"),    # pip / HuggingFace / torch / conda caches
        str(home / ".triton"),   # Triton JIT / autotune cache (kernel work)
        str(home / ".nv"),       # NVIDIA compute cache (ptxas / nvrtc)
        str(home / ".kube"),     # kubectl / oidc token cache (B200 access)
        "/tmp",
    ]
    # NOTE: ``sys.prefix`` (the active venv) is deliberately NOT writable — its
    # site-packages auto-runs ``.pth`` / ``sitecustomize.py`` at interpreter
    # start, so granting write there is a sandbox escape (and on a non-editable
    # install it is also the package source). Mission deps must install to a
    # workdir-local target (e.g. ``pip install --target <workdir>/.pylibs``),
    # never the live env; pip's download cache (~/.cache) stays writable so
    # cached installs still work.
    if os.path.isdir("/scratch"):
        candidates.append("/scratch")
    # Resolve symlinks on BOTH sides before the forbidden check (and hand codex
    # the real inode): a prior sandboxed session can write ~/.cache, so it could
    # repoint an allowlisted dir at the venv / gate brain via symlink and escape
    # on the next spawn. Comparing real paths closes that cross-session vector.
    forbidden = [os.path.realpath(f) for f in forbidden_write_roots(life_root=life_root)]
    out: list[str] = []
    for c in candidates:
        try:
            cp = os.path.realpath(c)
        except Exception:  # pragma: no cover — defensive
            cp = str(Path(c))
        if _is_forbidden(cp, forbidden):
            continue
        if cp not in out:
            out.append(cp)
    return out


# Environment keys carrying push-capable VCS credentials. A sandboxed builder
# role must NOT inherit these, else the engineer can ``gh push`` / ``git push``
# to the package's own remote and rewrite the harness at its source of truth,
# bypassing the local-FS sandbox entirely (network egress is not policed).
_VCS_CRED_ENV_KEYS = (
    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
    "GITHUB_API_TOKEN", "COPILOT_GITHUB_TOKEN", "GIT_TOKEN", "GIT_ASKPASS",
    "SSH_AUTH_SOCK",
)


def sandboxed_child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Child env for a sandboxed codex role.

    (1) Drops push-capable VCS credentials so a sandboxed engineer cannot push
        to the package's own remote and rewrite the harness at its source of
        truth (the local-FS sandbox does not police network egress).
    (2) Sets ``PYTHONSAFEPATH=1`` so a workdir ``code/sitecustomize.py`` or an
        earlier ``sys.path`` entry cannot shadow the package at import time.
    """
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key in _VCS_CRED_ENV_KEYS or key.startswith(("GH_", "GITHUB_")):
            env.pop(key, None)
    env["PYTHONSAFEPATH"] = "1"
    return env


def _resolver_config_target() -> Path | None:
    """Return the real resolver file when /etc/resolv.conf is a symlink."""
    try:
        target = Path("/etc/resolv.conf").resolve(strict=True)
    except OSError:
        return None
    return target if target != Path("/etc/resolv.conf") else None


def isolated_workdir_command(
    command: list[str],
    *,
    working_dir: str | os.PathLike[str] | None,
) -> list[str]:
    """Wrap any role CLI in a read-only-root, worktree-write bubblewrap."""
    if os.name != "posix" or not working_dir:
        raise RuntimeError("worktree isolation requires POSIX and an explicit workdir")
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("worktree isolation requires bubblewrap")
    if not command:
        raise RuntimeError("worktree isolation requires a command")
    executable = Path(command[0]).expanduser()
    if not executable.is_absolute():
        resolved_executable = shutil.which(command[0])
        if not resolved_executable:
            raise RuntimeError(
                f"worktree isolation cannot resolve executable: {command[0]}"
            )
        executable = Path(resolved_executable)
        command = [str(executable), *command[1:]]
    executable = executable.resolve()
    root = os.path.realpath(os.fspath(working_dir))
    if not os.path.isdir(root):
        raise RuntimeError("isolated workdir does not exist")
    runtime_root = Path(root) / ".argus-self-maintenance-runtime"
    private_copilot = runtime_root / "copilot-home"
    private_state = private_copilot / "session-state"
    private_state.mkdir(parents=True, exist_ok=True)
    copilot_home = Path.home() / ".copilot"
    for name in ("config.json", "settings.json", "permissions-config.json"):
        source = copilot_home / name
        target = private_copilot / name
        if source.is_file():
            shutil.copy2(source, target)

    wrapped = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-ipc",
        "--ro-bind",
        "/",
        "/",
        "--tmpfs",
        "/tmp",
    ]
    hidden_roots = (
        "/root",
        "/home",
        "/data",
        "/scratch",
        "/mnt",
        "/workspace",
        "/run",
        "/var/lib/kubelet",
        "/etc/kubernetes",
        "/etc/azure",
    )
    for hidden in hidden_roots:
        if os.path.isdir(hidden):
            wrapped.extend(["--tmpfs", hidden])

    mount_roots = [
        Path(value)
        for value in ("/tmp", *hidden_roots)
        if os.path.isdir(value)
    ]
    created_dirs: set[str] = {str(value) for value in mount_roots}

    def ensure_dir_chain(path: Path) -> None:
        matching = [
            candidate
            for candidate in mount_roots
            if path == candidate or candidate in path.parents
        ]
        if not matching:
            return
        current = max(matching, key=lambda value: len(value.parts))
        relative = path.relative_to(current)
        for part in relative.parts:
            current /= part
            rendered = str(current)
            if rendered not in created_dirs:
                wrapped.extend(["--dir", rendered])
                created_dirs.add(rendered)

    def bind_dir(source: Path, target: Path, *, writable: bool) -> None:
        if not source.is_dir():
            return
        ensure_dir_chain(target)
        wrapped.extend([
            "--bind" if writable else "--ro-bind",
            str(source),
            str(target),
        ])

    def bind_file(source: Path, target: Path) -> None:
        if not source.is_file():
            return
        ensure_dir_chain(target.parent)
        wrapped.extend(["--ro-bind", str(source), str(target)])

    # /etc/resolv.conf commonly points into /run, which is hidden above.
    # Re-expose only that symlink target so isolated backends retain DNS.
    resolver_target = _resolver_config_target()
    if resolver_target is not None:
        bind_file(resolver_target, resolver_target)

    worktree = Path(root)
    bind_dir(worktree, worktree, writable=True)
    git_entry = worktree / ".git"
    if git_entry.is_file():
        # Linked worktrees store a writable `gitdir: ...` pointer here. Keep
        # the pointer immutable so a confined role cannot redirect the daemon's
        # later unsandboxed validation/commit commands to attacker metadata.
        bind_file(git_entry, git_entry)
    bind_dir(Path(sys.prefix), Path(sys.prefix), writable=False)
    # Hidden roots such as /home are replaced with tmpfs above. Re-expose only
    # the selected backend executable so configured per-user CLI installs remain
    # runnable without revealing the rest of their home directory.
    bind_file(executable, executable)
    bind_dir(
        Path.home() / ".cache" / "copilot",
        Path.home() / ".cache" / "copilot",
        writable=False,
    )
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=worktree,
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        common = ""
    if common:
        common_path = Path(common)
        bind_dir(common_path, common_path, writable=False)
    bind_dir(private_copilot, Path.home() / ".copilot", writable=False)
    bind_dir(private_state, Path.home() / ".copilot" / "session-state", writable=True)
    for index, value in enumerate(command[:-1]):
        if value == "--output-schema":
            schema = Path(command[index + 1]).expanduser().resolve()
            bind_file(schema, schema)

    wrapped.extend([
        "--dev-bind",
        "/dev",
        "/dev",
        "--proc",
        "/proc",
        "--chdir",
        root,
    ])
    return [*wrapped, "--", *command]


def codex_sandbox_args(
    *,
    working_dir: str | os.PathLike[str] | None = None,
    life_root: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Codex CLI sandbox args for a RAW spawn (a subagent supervisor / report
    author that does NOT go through AgentCliRunner). Returns the workspace-write
    sandbox flags when the gate is on, else the legacy dangerous bypass — so raw
    spawns stay consistent with the run_exec chokepoint and no spawn site is
    left un-contained when the operator enables the sandbox."""
    mode = engineer_sandbox_mode()
    if mode is None:
        return ["--dangerously-bypass-approvals-and-sandbox"]
    args = ["-s", mode]
    # Always pin ``-C``. Without it, workspace-write roots its writable workspace
    # at the inherited cwd (the daemon's ``/``), exposing the whole FS — so fall
    # closed to a private scratch dir rather than emit a rootless ``-s``.
    args += ["-C", str(working_dir) if working_dir else fail_closed_workdir()]
    for extra in writable_roots(life_root=life_root):
        args += ["--add-dir", extra]
    if mode == "workspace-write":
        args += ["-c", "sandbox_workspace_write.network_access=true"]
    return args


def codex_sandbox_env() -> dict[str, str] | None:
    """Child env for a RAW sandboxed codex spawn (scrubbed creds +
    PYTHONSAFEPATH), or ``None`` to inherit the parent env (gate off)."""
    if engineer_sandbox_mode() is None:
        return None
    return sandboxed_child_env()
