from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fcntl  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    import msvcrt  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None


@dataclass
class TokenLock:
    token_hash: str
    lock_path: Path
    meta_path: Path
    fd: int

    def release(self) -> None:
        try:
            _unlock_file(self.fd)
        finally:
            os.close(self.fd)
        try:
            self.meta_path.unlink(missing_ok=True)
        except Exception:
            pass


def default_token_lock_dir() -> str:
    candidates = [
        Path(tempfile.gettempdir()) / "argusbot-token-locks",
        Path.cwd() / ".argusbot-token-locks",
    ]
    for candidate in candidates:
        if _can_prepare_dir(candidate):
            return str(candidate)
    return str(candidates[-1])


def acquire_token_lock(*, token: str, owner_info: dict[str, Any], lock_dir: str | Path | None = None) -> TokenLock:
    directory = Path(lock_dir or default_token_lock_dir()).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
    lock_path = directory / f"{digest}.lock"
    meta_path = directory / f"{digest}.json"

    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    _ensure_lockfile_content(fd)
    try:
        _lock_file(fd)
    except OSError as exc:
        os.close(fd)
        detail = _read_meta(meta_path)
        owner_desc = json.dumps(detail, ensure_ascii=True) if detail else "unknown owner"
        raise RuntimeError(
            "Telegram token is already in use by another daemon. "
            f"owner={owner_desc}"
        ) from exc

    payload = dict(owner_info)
    payload["token_hash"] = digest
    payload["lock_path"] = str(lock_path)
    meta_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return TokenLock(token_hash=digest, lock_path=lock_path, meta_path=meta_path, fd=fd)


def _ensure_lockfile_content(fd: int) -> None:
    try:
        stat = os.fstat(fd)
    except OSError:
        return
    if stat.st_size > 0:
        return
    os.write(fd, b"0")
    os.lseek(fd, 0, os.SEEK_SET)


def _lock_file(fd: int) -> None:
    if os.name == "nt":
        if msvcrt is None:  # pragma: no cover - safety net
            raise OSError("msvcrt not available")
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return
    if fcntl is None:  # pragma: no cover - safety net
        raise OSError("fcntl not available")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(fd: int) -> None:
    if os.name == "nt":
        if msvcrt is None:  # pragma: no cover - safety net
            return
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    if fcntl is None:  # pragma: no cover - safety net
        return
    fcntl.flock(fd, fcntl.LOCK_UN)


def _read_meta(meta_path: Path) -> dict[str, Any] | None:
    if not meta_path.exists():
        return None
    try:
        raw = meta_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _can_prepare_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    probe = path / ".write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return False
    return True
