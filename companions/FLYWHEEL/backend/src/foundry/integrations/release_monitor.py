"""Read-only Argus upstream release monitor.

This module only compares immutable commit ids. It intentionally has no clone,
pull, reset, checkout, daemon upgrade, or adoption operation.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .release_stager import (
    hardened_git_environment,
    parse_ls_remote,
    validate_ref,
    validate_repository,
)

Runner = Callable[..., subprocess.CompletedProcess[str]]
_REGISTRY_LOCK = threading.Lock()


class ReleaseRegistryError(ValueError):
    def __init__(self, code: str, message: str, *, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class RemoteReleaseStatus:
    repository: str
    ref: str
    remote_sha: str | None
    reported_sha: str | None
    stable_sha: str | None
    candidate_sha: str | None
    candidate_available: bool
    checked_at: str
    status: str
    error: str | None
    staging: str = "isolated_stage_available_confirmation_required"
    canary: str = "not_run"
    adoption: str = "human_approval_required_for_new_campaigns_only"


class ReleaseMonitor:
    def __init__(self, runner: Runner = subprocess.run, timeout: float = 8.0) -> None:
        self.runner = runner
        self.timeout = timeout

    def inspect(
        self,
        repository: str,
        *,
        ref: str = "refs/heads/main",
        reported_release: Mapping[str, Any] | None = None,
        release_registry: Path | None = None,
    ) -> RemoteReleaseStatus:
        repository = validate_repository(repository)
        ref = validate_ref(ref)
        stable_sha = None
        if release_registry and release_registry.is_file():
            try:
                registry = json.loads(release_registry.read_text(encoding="utf-8"))
                stable_sha = registry.get("stable_sha")
            except (OSError, json.JSONDecodeError):
                stable_sha = None
        reported_sha = str((reported_release or {}).get("commit_sha") or "") or None
        argv: Sequence[str] = ("git", "ls-remote", repository, ref)
        try:
            completed = self.runner(
                argv, capture_output=True, text=True, check=False,
                timeout=self.timeout, shell=False, env=hardened_git_environment(),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            return self._result(repository, ref, None, reported_sha, stable_sha, "error", str(exc))
        if completed.returncode != 0:
            return self._result(
                repository, ref, None, reported_sha, stable_sha, "error",
                (completed.stderr or "git ls-remote failed").strip(),
            )
        try:
            remote_sha = parse_ls_remote(completed.stdout, ref)
        except ValueError as exc:
            return self._result(repository, ref, None, reported_sha, stable_sha, "error", str(exc))
        candidate = remote_sha not in {reported_sha, stable_sha}
        return RemoteReleaseStatus(
            repository=repository, ref=ref, remote_sha=remote_sha,
            reported_sha=reported_sha, stable_sha=stable_sha,
            candidate_sha=remote_sha if candidate else None,
            candidate_available=candidate,
            checked_at=datetime.now(UTC).isoformat(),
            status="candidate" if candidate else "current",
            error=None,
        )

    @staticmethod
    def _result(
        repository: str, ref: str, remote_sha: str | None, reported_sha: str | None,
        stable_sha: str | None, status: str, error: str | None,
    ) -> RemoteReleaseStatus:
        return RemoteReleaseStatus(
            repository, ref, remote_sha, reported_sha, stable_sha, None, False,
            datetime.now(UTC).isoformat(), status, error,
        )


def persist_release_inspection(
    registry_path: Path,
    inspection: Mapping[str, Any],
) -> dict[str, Any]:
    """Atomically publish inspection fields without changing stable/canary state."""

    required = ("repository", "ref", "checked_at", "status")
    if any(not isinstance(inspection.get(key), str) or not inspection.get(key) for key in required):
        raise ReleaseRegistryError(
            "invalid_inspection",
            "release inspection is missing required registry fields",
            http_status=500,
        )
    inspection_fields = (
        "repository",
        "ref",
        "remote_sha",
        "reported_sha",
        "candidate_sha",
        "candidate_available",
        "checked_at",
        "status",
        "error",
    )
    record = {key: inspection.get(key) for key in inspection_fields}
    path = Path(registry_path)

    with _REGISTRY_LOCK:
        registry: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ReleaseRegistryError(
                    "invalid_registry",
                    "existing release registry is invalid; refusing to overwrite stable/canary state",
                    http_status=409,
                ) from exc
            except OSError as exc:
                raise ReleaseRegistryError(
                    "registry_read_failed",
                    "release registry could not be read",
                    http_status=500,
                ) from exc
            if not isinstance(existing, dict):
                raise ReleaseRegistryError(
                    "invalid_registry",
                    "existing release registry is not an object; refusing to overwrite stable/canary state",
                    http_status=409,
                )
            registry.update(existing)

        # Deliberately update only remote-inspection fields.  stable_sha,
        # canary_sha and any richer stable/canary records remain byte-for-byte
        # equivalent JSON values from the existing registry.
        registry.setdefault("schema_version", 1)
        registry.update(record)
        registry["last_inspection"] = dict(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(registry, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        except OSError as exc:
            raise ReleaseRegistryError(
                "registry_write_failed",
                "release inspection succeeded but its registry update failed",
                http_status=500,
            ) from exc
    return registry
