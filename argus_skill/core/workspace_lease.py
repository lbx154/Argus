"""Host-local exclusive leases for agent execution workspaces."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]


class WorkspaceLeaseBusy(RuntimeError):
    """Another live process already owns the canonical workdir."""


def canonical_workdir(workdir: str | Path) -> Path:
    resolved = Path(workdir).expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise NotADirectoryError(f"workdir is not a directory: {resolved}")
    return resolved


def workspace_lease_path(workdir: str | Path) -> Path:
    canonical = canonical_workdir(workdir)
    uid = os.getuid() if hasattr(os, "getuid") else 0
    root = Path(tempfile.gettempdir()) / f"argus-skill-workspaces-{uid}"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    lease_dir = root.joinpath(*canonical.parts[1:])
    lease_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return lease_dir / "lease.lock"


def _busy_message(canonical: Path, detail: str) -> str:
    """Say who holds the lease and what to do, not just that it is held.

    The raw lease record was previously appended verbatim, so an operator who
    launched a second daemon in the same directory got a line of JSON with a pid
    in it and no next step. Everything needed was present; none of it was
    actionable.
    """
    base = f"workdir {canonical} is already leased"
    owner: dict[str, Any] = {}
    if detail:
        try:
            parsed = json.loads(detail)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            owner = parsed
    if not owner:
        return f"{base}{f': {detail}' if detail else ''}"
    pid = owner.get("pid")
    lines = [f"{base} by pid {pid}" if pid else base]
    sid = str(owner.get("sid") or "").strip()
    if sid:
        lines.append(f"  session: {sid}")
    life_dir = str(owner.get("life_dir") or "").strip()
    if life_dir:
        lines.append(f"  project: {life_dir}")
    lines.append("  a workdir runs one daemon at a time. Either:")
    lines.append("    - watch the one already there:  argus --status   (or --follow)")
    if pid:
        lines.append(f"    - stop it:                      kill {pid}")
    lines.append("    - or start this objective in a different directory")
    return "\n".join(lines)


def acquire_workspace_lease(
    workdir: str | Path,
    *,
    owner: dict[str, Any] | None = None,
) -> int | None:
    """Acquire a non-blocking exclusive lease and return its open fd."""
    if fcntl is None:
        return None
    canonical = canonical_workdir(workdir)
    path = workspace_lease_path(canonical)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        detail = ""
        try:
            detail = os.pread(fd, 4096, 0).decode("utf-8", errors="replace").strip()
        except OSError:
            pass
        os.close(fd)
        raise WorkspaceLeaseBusy(
            _busy_message(canonical, detail)
        ) from exc
    payload = {
        "workdir": str(canonical),
        "pid": os.getpid(),
        **(owner or {}),
    }
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode()
    os.ftruncate(fd, 0)
    os.pwrite(fd, encoded, 0)
    os.fsync(fd)
    return fd


def release_workspace_lease(fd: int | None, *, unlock: bool = True) -> None:
    if fd is None:
        return
    try:
        if unlock and fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


__all__ = [
    "WorkspaceLeaseBusy",
    "acquire_workspace_lease",
    "canonical_workdir",
    "release_workspace_lease",
    "workspace_lease_path",
]
