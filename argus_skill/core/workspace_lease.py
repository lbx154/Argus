"""Host-local exclusive leases for agent execution workspaces."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import portalocker


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
    if os.name == "nt":
        # Mirroring an absolute C:\... path below the temp root quickly hits
        # MAX_PATH in nested CI/user profiles.  Windows paths are also
        # case-insensitive, so hash their normalized canonical spelling into a
        # short, stable lock name instead.
        identity = os.path.normcase(str(canonical))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        leaf = "".join(
            character if character.isalnum() or character in "-_" else "-"
            for character in canonical.name
        ).strip("-_")[:32] or "workspace"
        return root / f"{leaf}-{digest[:32]}.lock"
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
    if sid:
        lines.append(
            "    - stop it safely:               "
            f"argus --daemon-stop --resume {sid}"
        )
    lines.append("    - or start this objective in a different directory")
    return "\n".join(lines)


def _windows_owner_path(lock_path: Path) -> Path:
    return lock_path.with_suffix(".owner.json")


def _read_owner_detail(lock_path: Path, fd: int) -> str:
    try:
        if os.name == "nt":
            # Windows byte-range locks also reject reads through a second file
            # descriptor. Keep diagnostics beside (not inside) the locked
            # range so a rejected launcher can still name the live owner.
            return _windows_owner_path(lock_path).read_text(
                encoding="utf-8", errors="replace"
            ).strip()
        os.lseek(fd, 0, os.SEEK_SET)
        return os.read(fd, 4096).decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _write_windows_owner(lock_path: Path, encoded: bytes) -> None:
    owner_path = _windows_owner_path(lock_path)
    temporary = owner_path.with_name(
        f".{owner_path.name}.{os.getpid()}.{id(encoded)}.tmp"
    )
    try:
        temporary.write_bytes(encoded)
        os.replace(temporary, owner_path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def acquire_workspace_lease(
    workdir: str | Path,
    *,
    owner: dict[str, Any] | None = None,
) -> int | None:
    """Acquire a non-blocking exclusive lease and return its open fd."""
    canonical = canonical_workdir(workdir)
    path = workspace_lease_path(canonical)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        portalocker.lock(fd, portalocker.LOCK_EX | portalocker.LOCK_NB)
    except portalocker.exceptions.LockException as exc:
        detail = _read_owner_detail(path, fd)
        os.close(fd)
        raise WorkspaceLeaseBusy(
            _busy_message(canonical, detail)
        ) from exc
    try:
        payload = {
            "workdir": str(canonical),
            "pid": os.getpid(),
            **(owner or {}),
        }
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode()
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, encoded)
        os.fsync(fd)
        if os.name == "nt":
            _write_windows_owner(path, encoded)
    except BaseException:
        # Ownership is transferred to the caller only after initialization.
        # Closing releases the OS lock even when the Windows sidecar is busy.
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return fd


def release_workspace_lease(fd: int | None, *, unlock: bool = True) -> None:
    if fd is None:
        return
    try:
        if unlock:
            portalocker.unlock(fd)
    finally:
        os.close(fd)


__all__ = [
    "WorkspaceLeaseBusy",
    "acquire_workspace_lease",
    "canonical_workdir",
    "release_workspace_lease",
    "workspace_lease_path",
]
