"""Isolated, content-addressed staging for an explicitly confirmed Git release.

The stager is deliberately narrower than an updater.  It never opens or
changes an existing checkout, never adopts a release, and never starts Argus.
All writes stay below ``<data_dir>/releases/staging/<full-sha>`` (plus one
per-request audit record) and all Git calls use explicit argv with
``shell=False``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import unquote, urlsplit

Runner = Callable[..., subprocess.CompletedProcess[str]]
Clock = Callable[[], datetime]

_FULL_SHA = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_SAFE_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
_SAFE_USER = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_REMOTE_PATH = re.compile(r"^/?[A-Za-z0-9._~+/@-]+$")
_SCP_REMOTE = re.compile(
    r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9.-]+):(?P<path>[A-Za-z0-9._~+/@-]+)$"
)
_REF_INVALID = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def hardened_git_environment(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a non-interactive Git environment isolated from host Git config.

    Release inspection and staging share this helper so a read-only probe cannot
    accidentally inherit a worktree, credential helper, hook, proxy, or config
    injection that the staging path rejects.
    """

    environment = dict(os.environ if source is None else source)
    for name in tuple(environment):
        if name.upper().startswith("GIT_") or name.upper() in {
            "GCM_INTERACTIVE",
            "SSH_ASKPASS",
        }:
            environment.pop(name, None)
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    return environment


class ReleaseStageError(ValueError):
    """A stable, API-safe staging failure with an optional audit attempt id."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 422,
        attempt_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.attempt_id = attempt_id


@dataclass(frozen=True)
class StagedRelease:
    repository: str
    ref: str
    sha: str
    status: str
    reused: bool
    stage_dir: str
    source_dir: str
    manifest_path: str
    attempt_id: str
    manifest: Mapping[str, Any]


def validate_repository(repository: str) -> str:
    """Accept only explicit network Git remotes with conservative characters."""

    value = repository.strip()
    if not value or value != repository or any(ord(char) < 32 for char in value):
        raise ReleaseStageError("invalid_repository", "repository must be an explicit remote URL")

    scp = _SCP_REMOTE.fullmatch(value)
    if scp:
        _validate_host(scp.group("host"))
        _validate_remote_path(scp.group("path"))
        return value

    try:
        parsed = urlsplit(value)
        username = parsed.username
        password = parsed.password
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ReleaseStageError("invalid_repository", "repository URL is malformed") from exc

    if parsed.scheme not in {"https", "ssh", "git"}:
        raise ReleaseStageError(
            "invalid_repository",
            "repository must use an explicit https://, ssh://, git://, or user@host:path remote",
        )
    if not hostname or parsed.query or parsed.fragment:
        raise ReleaseStageError("invalid_repository", "repository URL must have one host and no query or fragment")
    if password is not None or (parsed.scheme in {"https", "git"} and username is not None):
        raise ReleaseStageError("credentials_in_repository", "credentials are forbidden in repository URLs")
    if username is not None and not _SAFE_USER.fullmatch(username):
        raise ReleaseStageError("invalid_repository", "repository SSH username is invalid")
    _validate_host(hostname)
    _validate_remote_path(parsed.path)
    return value


def validate_ref(ref: str) -> str:
    """Allow exact branch or tag refs, never refspecs or option-like values."""

    value = ref.strip()
    if value != ref or len(value) > 255 or not value.startswith(("refs/heads/", "refs/tags/")):
        raise ReleaseStageError("invalid_ref", "ref must be an exact refs/heads/* or refs/tags/* name")
    suffix = value.split("/", 2)[-1]
    segments = suffix.split("/")
    if (
        _REF_INVALID.search(value)
        or ".." in value
        or "@{" in value
        or "//" in value
        or value.endswith((".", "/"))
        or any(
            not segment
            or segment in {".", "..", "@"}
            or segment.startswith((".", "-"))
            or segment.endswith(".")
            or segment.lower().endswith(".lock")
            for segment in segments
        )
    ):
        raise ReleaseStageError("invalid_ref", "ref contains an unsafe or invalid Git ref component")
    return value


def validate_full_sha(expected_sha: str) -> str:
    value = expected_sha.strip().lower()
    if value != expected_sha.lower() or not _FULL_SHA.fullmatch(value):
        raise ReleaseStageError("invalid_sha", "expected_sha must be a full 40- or 64-character hexadecimal SHA")
    return value


def parse_ls_remote(stdout: str, expected_ref: str) -> str:
    matches: list[str] = []
    for raw_line in stdout.splitlines():
        columns = raw_line.split()
        if len(columns) != 2 or columns[1] != expected_ref:
            continue
        if not _FULL_SHA.fullmatch(columns[0]):
            raise ReleaseStageError("invalid_remote_response", "git ls-remote returned a non-full SHA", http_status=502)
        matches.append(columns[0].lower())
    if len(set(matches)) != 1:
        raise ReleaseStageError("remote_ref_not_found", "remote ref did not resolve to one full SHA", http_status=502)
    return matches[0]


class ReleaseStager:
    """Stage one verified commit in a newly-created, isolated directory."""

    def __init__(
        self,
        data_dir: Path,
        *,
        runner: Runner = subprocess.run,
        timeout: float = 120.0,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.runner = runner
        self.timeout = max(1.0, float(timeout))
        self.clock = clock

    def stage(
        self,
        repository: str,
        *,
        ref: str,
        expected_sha: str,
        confirm_isolated_stage: bool,
    ) -> StagedRelease:
        if confirm_isolated_stage is not True:
            raise ReleaseStageError(
                "confirmation_required",
                "confirm_isolated_stage=true is required; staging never implies adoption",
            )
        repository = validate_repository(repository)
        ref = validate_ref(ref)
        expected_sha = validate_full_sha(expected_sha)

        releases_root = self.data_dir / "releases"
        staging_root = releases_root / "staging"
        attempts_root = releases_root / "attempts"
        self._ensure_plain_directory(releases_root)
        self._ensure_plain_directory(staging_root)
        self._ensure_plain_directory(attempts_root)

        attempt_id = f"{self.clock().strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:12]}"
        attempt_path = attempts_root / f"{attempt_id}.json"
        started_at = self._now()
        attempt: dict[str, Any] = {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "repository": repository,
            "ref": ref,
            "expected_sha": expected_sha,
            "started_at": started_at,
            "completed_at": None,
            "status": "checking_remote",
            "commands": [],
            "stage_dir": None,
            "error": None,
        }
        self._write_json(attempt_path, attempt)

        target: Path | None = None
        manifest_path: Path | None = None
        manifest: dict[str, Any] | None = None
        try:
            remote_sha = self._remote_sha(repository, ref, attempt)
            remote_verified_at = self._now()
            attempt["remote_sha"] = remote_sha
            if remote_sha != expected_sha:
                raise ReleaseStageError(
                    "sha_mismatch",
                    f"expected_sha does not match the current remote ref ({remote_sha})",
                    http_status=409,
                )

            target = self._content_path(staging_root, expected_sha)
            manifest_path = target / "manifest.json"
            if target.exists():
                result = self._reuse_if_complete(
                    target, manifest_path, repository, ref, expected_sha, attempt_id, attempt
                )
                self._write_json(attempt_path, attempt)
                return result

            try:
                target.mkdir(mode=0o700, parents=False, exist_ok=False)
            except FileExistsError as exc:
                raise ReleaseStageError(
                    "stage_conflict",
                    "the content-addressed stage path was created concurrently; inspect its manifest before retrying",
                    http_status=409,
                ) from exc
            if self._is_link_like(target):
                raise ReleaseStageError("unsafe_stage_path", "stage path must not be a link or junction", http_status=409)

            source_dir = target / "source"
            hooks_dir = target / "disabled-hooks"
            template_dir = target / "empty-template"
            source_dir.mkdir(mode=0o700)
            hooks_dir.mkdir(mode=0o700)
            template_dir.mkdir(mode=0o700)
            manifest = self._initial_manifest(repository, ref, expected_sha, started_at)
            self._write_json(manifest_path, manifest)
            attempt["status"] = "staging"
            attempt["stage_dir"] = str(target)
            self._write_json(attempt_path, attempt)

            common = ("git", "-c", "protocol.file.allow=never")
            self._run(
                (*common, "init", "--quiet", f"--template={template_dir}", str(source_dir)),
                attempt,
                operation="init_isolated_repository",
            )
            self._run(
                (*common, "-C", str(source_dir), "remote", "add", "origin", repository),
                attempt,
                operation="configure_origin",
            )
            self._run(
                (
                    *common, "-c", f"core.hooksPath={hooks_dir}", "-C", str(source_dir),
                    "fetch", "--no-tags", "--depth=1", "--filter=blob:none", "origin", ref,
                ),
                attempt,
                operation="fetch_verified_ref",
            )
            self._run(
                (
                    *common, "-c", f"core.hooksPath={hooks_dir}", "-c", "advice.detachedHead=false",
                    "-C", str(source_dir), "checkout", "--detach", "--quiet", expected_sha,
                ),
                attempt,
                operation="checkout_exact_detached_sha",
            )
            verified = self._run(
                (*common, "-C", str(source_dir), "rev-parse", "--verify", "HEAD"),
                attempt,
                operation="verify_checked_out_sha",
            ).stdout.strip().lower()
            if verified != expected_sha:
                raise ReleaseStageError(
                    "checkout_verification_failed",
                    "staged HEAD did not match expected_sha",
                    http_status=502,
                )

            manifest.update(
                {
                    "status": "staged",
                    "completed_at": self._now(),
                    "remote_verified_at": remote_verified_at,
                    "checkout_verified": True,
                    "command_count": len(attempt["commands"]),
                }
            )
            self._write_json(manifest_path, manifest)
            attempt.update({"status": "staged", "completed_at": self._now(), "error": None})
            self._write_json(attempt_path, attempt)
            return StagedRelease(
                repository=repository,
                ref=ref,
                sha=expected_sha,
                status="staged",
                reused=False,
                stage_dir=str(target),
                source_dir=str(source_dir),
                manifest_path=str(manifest_path),
                attempt_id=attempt_id,
                manifest=manifest,
            )
        except ReleaseStageError as exc:
            exc.attempt_id = attempt_id
            self._persist_failure(exc, attempt, attempt_path, target, manifest_path, manifest)
            raise
        except OSError as raw_exc:
            exc = ReleaseStageError(
                "stage_io_failed",
                "isolated stage filesystem operation failed",
                http_status=500,
                attempt_id=attempt_id,
            )
            self._persist_failure(exc, attempt, attempt_path, target, manifest_path, manifest)
            raise exc from raw_exc

    def _remote_sha(self, repository: str, ref: str, attempt: dict[str, Any]) -> str:
        completed = self._run(
            ("git", "ls-remote", repository, ref),
            attempt,
            operation="read_only_remote_resolution",
        )
        return parse_ls_remote(completed.stdout, ref)

    def _persist_failure(
        self,
        exc: ReleaseStageError,
        attempt: dict[str, Any],
        attempt_path: Path,
        target: Path | None,
        manifest_path: Path | None,
        manifest: dict[str, Any] | None,
    ) -> None:
        completed_at = self._now()
        attempt.update(
            {
                "status": "failed" if exc.http_status >= 500 else "rejected",
                "completed_at": completed_at,
                "error": {"code": exc.code, "message": str(exc)},
            }
        )
        try:
            self._write_json(attempt_path, attempt)
            if target is not None and manifest_path is not None and manifest is not None:
                manifest.update(
                    {
                        "status": "failed",
                        "completed_at": completed_at,
                        "error": {"code": exc.code, "message": str(exc)},
                        "diagnostics": "diagnostics.json",
                    }
                )
                self._write_json(manifest_path, manifest)
                self._write_json(target / "diagnostics.json", attempt)
        except OSError:
            # The original error remains primary.  Earlier attempt/manifest
            # snapshots are intentionally left in place rather than cleaned.
            return

    def _run(
        self,
        argv: Sequence[str],
        attempt: dict[str, Any],
        *,
        operation: str,
    ) -> subprocess.CompletedProcess[str]:
        record: dict[str, Any] = {
            "operation": operation,
            "argv": list(argv),
            "started_at": self._now(),
            "completed_at": None,
            "returncode": None,
            "stdout": "",
            "stderr": "",
        }
        attempt["commands"].append(record)
        environment = hardened_git_environment()
        try:
            completed = self.runner(
                tuple(argv),
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout,
                shell=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            record.update({"completed_at": self._now(), "stderr": "command timed out"})
            raise ReleaseStageError("git_timeout", f"{operation} timed out", http_status=504) from exc
        except (FileNotFoundError, OSError) as exc:
            record.update({"completed_at": self._now(), "stderr": self._bounded(str(exc))})
            raise ReleaseStageError("git_unavailable", f"{operation} could not run Git", http_status=502) from exc
        record.update(
            {
                "completed_at": self._now(),
                "returncode": completed.returncode,
                "stdout": self._bounded(completed.stdout or ""),
                "stderr": self._bounded(completed.stderr or ""),
            }
        )
        if completed.returncode != 0:
            raise ReleaseStageError(
                "git_command_failed",
                f"{operation} failed with exit code {completed.returncode}",
                http_status=502,
            )
        return completed

    def _reuse_if_complete(
        self,
        target: Path,
        manifest_path: Path,
        repository: str,
        ref: str,
        sha: str,
        attempt_id: str,
        attempt: dict[str, Any],
    ) -> StagedRelease:
        if self._is_link_like(target):
            raise ReleaseStageError("unsafe_stage_path", "existing stage path is a link or junction", http_status=409)
        manifest = self._load_json(manifest_path)
        source = target / "source"
        git_metadata = source / ".git"
        matches = (
            manifest.get("status") == "staged"
            and manifest.get("repository") == repository
            and manifest.get("ref") == ref
            and manifest.get("sha") == sha
            and source.is_dir()
            and git_metadata.exists()
            and not self._is_link_like(source)
            and not self._is_link_like(git_metadata)
        )
        if not matches:
            raise ReleaseStageError(
                "stage_conflict",
                "content-addressed stage path exists but is incomplete, unsafe, or belongs to another remote/ref",
                http_status=409,
            )
        attempt.update(
            {
                "status": "reused",
                "completed_at": self._now(),
                "stage_dir": str(target),
                "error": None,
            }
        )
        return StagedRelease(
            repository=repository,
            ref=ref,
            sha=sha,
            status="staged",
            reused=True,
            stage_dir=str(target),
            source_dir=str(source),
            manifest_path=str(manifest_path),
            attempt_id=attempt_id,
            manifest=manifest,
        )

    @staticmethod
    def _initial_manifest(repository: str, ref: str, sha: str, created_at: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "argus_isolated_release_stage",
            "repository": repository,
            "ref": ref,
            "sha": sha,
            "status": "staging",
            "created_at": created_at,
            "completed_at": None,
            "source_relpath": "source",
            "checkout_mode": "detached_exact_sha",
            "tests": {
                "status": "not_run",
                "reason": "stage_only_default; tests and canary require a separate explicit workflow",
            },
            "adoption": {"status": "not_adopted", "requires_human_approval": True},
            "daemon": {"status": "not_started", "automatic_start": False},
            "running_campaigns_mutated": False,
            "safety": {
                "content_addressed": True,
                "shell": False,
                "existing_checkout_operations": [],
                "forbidden_operations": ["pull", "reset"],
            },
        }

    @staticmethod
    def _content_path(staging_root: Path, sha: str) -> Path:
        root = staging_root.resolve()
        target = (root / sha).resolve()
        if target.parent != root or target.name != sha:
            raise ReleaseStageError("unsafe_stage_path", "resolved stage path escaped the staging root")
        return target

    @classmethod
    def _ensure_plain_directory(cls, path: Path) -> None:
        if path.exists() and cls._is_link_like(path):
            raise ReleaseStageError("unsafe_stage_root", f"release directory must not be a link or junction: {path}")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not path.is_dir() or cls._is_link_like(path):
            raise ReleaseStageError("unsafe_stage_root", f"release path is not a plain directory: {path}")

    @staticmethod
    def _is_link_like(path: Path) -> bool:
        is_junction = getattr(path, "is_junction", None)
        return path.is_symlink() or bool(is_junction and is_junction())

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReleaseStageError("stage_conflict", "existing stage manifest is unreadable", http_status=409) from exc
        if not isinstance(value, dict):
            raise ReleaseStageError("stage_conflict", "existing stage manifest is invalid", http_status=409)
        return value

    @staticmethod
    def _write_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def _now(self) -> str:
        return self.clock().astimezone(UTC).isoformat(timespec="milliseconds")

    @staticmethod
    def _bounded(value: str, limit: int = 16_384) -> str:
        return value if len(value) <= limit else value[:limit] + "\n...[truncated]"


def _validate_host(host: str) -> None:
    if (
        host.startswith("-")
        or not _SAFE_HOST.fullmatch(host)
        or any(not component or component.startswith("-") or component.endswith("-") for component in host.split("."))
    ):
        raise ReleaseStageError("invalid_repository", "repository host is invalid")


def _validate_remote_path(path: str) -> None:
    decoded = unquote(path)
    components = decoded.replace("\\", "/").split("/")
    if (
        not decoded
        or not _SAFE_REMOTE_PATH.fullmatch(decoded)
        or "//" in decoded
        or decoded.endswith("/")
        or any(component in {".", ".."} or component.startswith("-") for component in components if component)
    ):
        raise ReleaseStageError("invalid_repository", "repository path contains unsafe characters or traversal")
