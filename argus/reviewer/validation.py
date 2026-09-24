"""Operator-scoped, read-only validation for an independent Reviewer."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from ..core.call_bound_execution import (
    PROCESS_REAP_SECONDS,
    CallBoundTasks,
    ExecutionCancelled,
    ProcessCleanupError,
    run_process,
)
from ..core.paths import global_root
from ..core.secret_guard import redact_secrets_text

READ_DIRS_ENV = "ARGUS_SKILL_REVIEWER_READ_DIRS"
IMAGE_ENV = "ARGUS_SKILL_REVIEWER_VALIDATION_IMAGE"
COMMAND_TOOL = "run_review_command"
RESULT_TOOL = "get_review_command"
CANCEL_TOOL = "cancel_review_command"
WAIT_SECONDS = 5.0
PREFLIGHT_SECONDS = 10.0
CLEANUP_SECONDS = 5.0
CLOSE_SECONDS = PREFLIGHT_SECONDS + CLEANUP_SECONDS + 2 * PROCESS_REAP_SECONDS + 1


class ReviewEnvironmentError(Exception):
    pass


def configured_read_dirs() -> list[str]:
    from ..core.knobs import resolve_knob

    raw = resolve_knob(READ_DIRS_ENV, "[]").value.strip()
    if not raw:
        return []
    values = json.loads(raw)
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError(f"{READ_DIRS_ENV} must be a JSON array of absolute directories")
    return list(dict.fromkeys(str(_read_root(value)) for value in values))


def _read_root(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Reviewer read roots must be absolute directories")
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError(f"Reviewer read root is not a directory: {path}")
    if any(char in str(path) for char in (",", "\n", "\r", "\0")):
        raise ValueError("Reviewer bind paths must not contain commas or control characters")
    protected = [Path.home().resolve(), global_root().resolve()]
    if path == Path(path.anchor) or any(root.is_relative_to(path) for root in protected):
        raise ValueError(f"Reviewer read root is too broad: {path}")
    return path


def _tail(path: Path, limit: int = 24_000) -> str:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - limit))
        return handle.read().decode("utf-8", errors="replace")


def _docker_output(
    prefix: list[str], args: list[str], *, env: dict[str, str],
    cancelled: threading.Event | None = None,
) -> str:
    result = run_process(
        [*prefix, *args], env=env, timeout=PREFLIGHT_SECONDS, cancelled=cancelled,
    )
    if result.returncode:
        raise ReviewEnvironmentError(
            f"Docker {' '.join(args[:2])} failed (exit {result.returncode}): "
            f"{redact_secrets_text(result.stderr)[-4000:]}"
        )
    return result.stdout.strip()


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(receipt, indent=2) + "\n")
        temporary.replace(path)
    except OSError as exc:
        receipt["receipt_error"] = redact_secrets_text(str(exc))
        raise RuntimeError("role tool request failed") from exc


class ReviewValidation:
    def __init__(self, workdir: str | None, read_dirs: list[str], image: str) -> None:
        if not workdir:
            raise ValueError("Reviewer validation requires an explicit workdir")
        self.workdir = _read_root(workdir)
        self.roots = list(dict.fromkeys([self.workdir, *map(_read_root, read_dirs)]))
        self.image = image.strip()
        if not self.image or self.image.startswith("-") or any(char.isspace() for char in self.image):
            raise ValueError("Reviewer validation requires one Docker image reference")
        self.output_root = global_root() / "reviewer-checks" / uuid.uuid4().hex
        self.output_root.mkdir(parents=True, mode=0o700)
        self.output_root = self.output_root.resolve()
        self.tasks = CallBoundTasks()
        self._receipts: dict[str, dict[str, Any]] = {}

    def tools(self) -> list[dict[str, Any]]:
        command = {
            "name": COMMAND_TOOL,
            "description": (
                "Run an independent validation command in a Linux Docker sandbox. "
                "The project and operator-approved inputs are read-only; network "
                "access is disabled. Only a fresh scratch directory is writable. "
                "Use {scratch} in argv for compiler/checker outputs. This records "
                "execution evidence, never a review decision. Full output is saved "
                f"under {self.output_root}. Missing tools or denied writes are "
                "environment failures, not proof failures. Waits at most five seconds; "
                f"if still running, use {RESULT_TOOL} with the returned command_id, "
                "not another run. Zero timeout means until completion or this review "
                "turn ends; ending the turn cancels unfinished commands."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "argv": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                    "timeout_seconds": {"type": "number", "minimum": 0, "default": 0},
                },
                "required": ["argv"],
                "additionalProperties": False,
            },
        }
        controls = [{
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": {"command_id": {"type": "string", "minLength": 1}},
                "required": ["command_id"],
                "additionalProperties": False,
            },
        } for name, description in (
            (RESULT_TOOL, "Read this turn's validation result, waiting at most five seconds. Running is not completion."),
            (CANCEL_TOOL, "Cancel this turn's validation command and read its result, waiting at most five seconds. Check again if cleanup is still running."),
        )]
        return [command, *controls]

    def result(self, command_id: str, *, cancel: bool = False) -> dict[str, Any]:
        if cancel:
            self.tasks.cancel(command_id)
        result = self.tasks.wait(command_id, WAIT_SECONDS)
        if result is None:
            result = json.loads((self.output_root / command_id / "result.json").read_text())
        return {
            **result,
            "stdout": _tail(Path(result["stdout_path"])),
            "stderr": _tail(Path(result["stderr_path"])),
        }

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        timeout = payload.get("timeout_seconds", 0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout_seconds must be a finite nonnegative number")
        argv = payload.get("argv")
        if not isinstance(argv, list) or not argv or not all(
            isinstance(arg, str) and arg and "\0" not in arg for arg in argv
        ):
            raise ValueError("argv must be a nonempty array of nonempty strings")

        directory = self.output_root / uuid.uuid4().hex
        directory.mkdir(mode=0o700)
        scratch = directory / "scratch"
        scratch.mkdir(mode=0o700)
        if any(char in str(scratch) for char in (",", "\n", "\r", "\0")):
            raise ValueError("Reviewer scratch bind path contains unsupported characters")
        for name in ("home", "tmp", "cache", "target"):
            (scratch / name).mkdir()
        expanded = [arg.replace("{scratch}", str(scratch)) for arg in argv]
        stdout_path, stderr_path = directory / "stdout.txt", directory / "stderr.txt"
        stdout_path.touch()
        stderr_path.touch()
        receipt_path = directory / "result.json"
        receipt: dict[str, Any] = {
            "command_id": directory.name, "receipt_path": str(receipt_path),
            "argv": expanded, "workdir": str(self.workdir), "image": self.image,
            "container": "argus-review-" + directory.name,
            "readonly_roots": [str(root) for root in self.roots],
            "scratch": str(scratch), "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path), "status": "running",
            "exit_code": None, "timeout_seconds": timeout,
        }
        self._receipts[directory.name] = receipt
        try:
            _write_receipt(receipt_path, receipt)
        except RuntimeError:
            receipt.update(status="not_started")
            raise
        try:
            self.tasks.submit(directory.name, lambda cancelled: self._execute(receipt, cancelled))
        except Exception:
            receipt.update(status="not_started")
            _write_receipt(receipt_path, receipt)
            raise
        return self.result(directory.name)

    def _execute(self, receipt: dict[str, Any], cancelled: threading.Event) -> dict[str, Any]:
        receipt_path = Path(receipt["receipt_path"])
        directory = receipt_path.parent
        cleanup_needed = False
        create_uncertain = False
        stage = "Docker setup"
        try:
            if not sys.platform.startswith("linux"):
                raise ReviewEnvironmentError("Read-only Reviewer validation currently requires Linux Docker")
            docker = shutil.which("docker")
            if not docker:
                raise ReviewEnvironmentError("Docker is unavailable; refusing unsandboxed Reviewer execution")
            endpoint = os.environ.get("DOCKER_HOST", "").strip()
            if os.environ.get("DOCKER_CONTEXT") or not endpoint:
                stage = "Docker context inspect"
                context_env = {key: os.environ[key] for key in (
                    "PATH", "HOME", "DOCKER_CONFIG", "DOCKER_CONTEXT",
                ) if key in os.environ}
                endpoint = _docker_output(
                    [docker], ["context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                    env=context_env, cancelled=cancelled,
                )
            if not endpoint.startswith("unix://"):
                raise ReviewEnvironmentError("Reviewer validation requires a local Unix-socket Docker daemon")
            # Resolve only the endpoint using the operator's context. All daemon
            # commands use a private, empty client config: Docker otherwise adds
            # proxy credentials independently of the explicit --env allowlist.
            client_config = directory / "docker-client"
            client_config.mkdir(mode=0o700)
            (client_config / "config.json").write_text("{}\n")
            docker_prefix = [docker, "--config", str(client_config), "--host", endpoint]
            docker_env = {"HOME": str(client_config), "PATH": os.defpath}
            receipt["docker_endpoint"] = endpoint
            stage = "Docker image inspect"
            image = _docker_output(
                docker_prefix, ["image", "inspect", "--format", "{{.Id}}", self.image],
                env=docker_env, cancelled=cancelled,
            )
            if not image.startswith("sha256:"):
                raise ReviewEnvironmentError("Docker did not return an immutable local image identity")
            receipt["image_id"] = image
            scratch = Path(receipt["scratch"])
            command = [
                *docker_prefix, "create", "--name", receipt["container"], "--init", "--pull", "never",
                "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "256",
                "--user", f"{os.getuid()}:{os.getgid()}",
                "--tmpfs", "/tmp:rw,nosuid,nodev",
            ]
            for root in self.roots:
                command += ["--mount", f"type=bind,src={root},dst={root},readonly"]
            command += ["--mount", f"type=bind,src={scratch},dst={scratch}"]
            environment = {
                "HOME": str(scratch / "home"),
                "TMPDIR": str(scratch / "tmp"),
                "XDG_CACHE_HOME": str(scratch / "cache"),
                "CARGO_TARGET_DIR": str(scratch / "target"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PATH": os.environ.get("PATH", os.defpath),
            }
            for key in ("RUSTUP_HOME", "RUSTUP_TOOLCHAIN"):
                if os.environ.get(key):
                    environment[key] = os.environ[key]
            for key, value in environment.items():
                command += ["--env", f"{key}={value}"]
            command += ["--workdir", str(self.workdir), image, *receipt["argv"]]
            if cancelled.is_set():
                raise ExecutionCancelled
            stage = "Docker create"
            cleanup_needed = create_uncertain = True
            # Let bounded creation finish before cancellation removes the named
            # container. A cancelled create RPC must never start work later.
            _docker_output(docker_prefix, command[len(docker_prefix):], env=docker_env)
            create_uncertain = False
            stage = "Docker start"
            _write_receipt(receipt_path, receipt)
            with Path(receipt["stdout_path"]).open("wb") as out, Path(receipt["stderr_path"]).open("wb") as err:
                result = run_process(
                    [*docker_prefix, "start", "--attach", receipt["container"]],
                    stdout=out, stderr=err, timeout=receipt["timeout_seconds"] or None,
                    env=docker_env, cancelled=cancelled,
                )
            if cancelled.is_set():
                raise ExecutionCancelled
            receipt.update(status="completed", exit_code=result.returncode)
        except ExecutionCancelled:
            receipt.update(status="cancelled")
        except ProcessCleanupError as exc:
            receipt.update(
                status=(
                    "cancelled" if isinstance(exc.interrupted, ExecutionCancelled)
                    else "timed_out" if isinstance(exc.interrupted, subprocess.TimeoutExpired) and stage == "Docker start"
                    else "execution_error"
                ),
                client_cleanup_error=str(exc),
            )
        except subprocess.TimeoutExpired:
            if stage == "Docker start":
                receipt.update(status="timed_out")
            else:
                receipt.update(status="environment_error", error=f"{stage} exceeded {PREFLIGHT_SECONDS:g} seconds.")
        except (ReviewEnvironmentError, OSError) as exc:
            receipt.update(status="environment_error", error=redact_secrets_text(f"{stage}: {exc}"))
        except Exception as exc:
            receipt.update(status="execution_error", error="role tool request failed")
            raise RuntimeError("role tool request failed") from exc
        finally:
            try:
                if cleanup_needed:
                    try:
                        cleanup = run_process(
                            [*docker_prefix, "rm", "--force", receipt["container"]],
                            env=docker_env, timeout=CLEANUP_SECONDS,
                        )
                        if cleanup.returncode and "No such container" not in cleanup.stderr:
                            raise ReviewEnvironmentError(redact_secrets_text(cleanup.stderr)[-4000:])
                        if create_uncertain:
                            raise ReviewEnvironmentError("Docker create was not confirmed; check the named container before reuse.")
                        receipt["cleanup_status"] = "removed" if cleanup.returncode == 0 else "absent"
                    except (ReviewEnvironmentError, OSError, subprocess.TimeoutExpired, ProcessCleanupError) as exc:
                        receipt.update(
                            cleanup_status="failed",
                            cleanup_error=redact_secrets_text(f"Docker cleanup failed: {exc}"),
                        )
                    except Exception as exc:
                        receipt.update(cleanup_status="failed", cleanup_error="role tool cleanup failed")
                        raise RuntimeError("role tool request failed") from exc
            finally:
                _write_receipt(receipt_path, receipt)
        return receipt

    def close(self) -> None:
        task_error = None
        try:
            self.tasks.close(CLOSE_SECONDS)
        except RuntimeError as exc:
            task_error = exc
        failures = [str(task_error)] if task_error is not None else []
        for record in list(self._receipts.values()):
            if record.get("cleanup_status") == "failed" or record.get("client_cleanup_error"):
                cleanup_error = "; ".join(
                    str(record[key]) for key in ("cleanup_error", "client_cleanup_error")
                    if record.get(key)
                )
                failures.append(
                    f"Reviewer container cleanup needs attention: {cleanup_error}; "
                    f"receipt {record['receipt_path']}"
                )
            if record.get("receipt_error"):
                failures.append(
                    f"Reviewer receipt persistence failed: {record['receipt_error']}; "
                    f"receipt {record['receipt_path']}"
                )
        if failures:
            raise RuntimeError("; ".join(failures)) from task_error


def configured_validation(workdir: str | None, read_dirs: list[str]) -> ReviewValidation | None:
    from ..core.knobs import resolve_knob

    image = resolve_knob(IMAGE_ENV, "").value.strip()
    return ReviewValidation(workdir, read_dirs, image) if image else None
