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
import sys
from pathlib import Path

_SANDBOX_ENV = "ARGUS_SKILL_ENGINEER_SANDBOX"
_VALID_MODES = {"workspace-write", "read-only", "danger-full-access"}


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
    home = Path.home()
    roots = [str(home / ".argus-skill"), str(home / ".codex")]
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


def writable_roots(*, life_root: str | os.PathLike[str] | None = None) -> list[str]:
    """``--add-dir`` allowlist for a sandboxed builder: the minimal set of
    out-of-cwd dirs autonomous research legitimately writes. The project workdir
    is the ``-C`` root (always writable) and is NOT included here. Any candidate
    under a forbidden root is dropped."""
    home = Path.home()
    candidates = [
        str(home / ".cache"),   # pip / HuggingFace / torch / conda caches
        sys.prefix,             # active python env prefix (so `pip install` works)
        str(home / ".kube"),    # kubectl/oidc token cache (B200 access)
        "/tmp",
    ]
    if os.path.isdir("/scratch"):
        candidates.append("/scratch")
    forbidden = forbidden_write_roots(life_root=life_root)
    out: list[str] = []
    for c in candidates:
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
    "GITHUB_API_TOKEN", "GIT_TOKEN", "GIT_ASKPASS", "SSH_AUTH_SOCK",
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
    if working_dir:
        args += ["-C", str(working_dir)]
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

