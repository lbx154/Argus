"""The single bounded 401 replay owner for agent-CLI provider calls."""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.secret_guard import redact_secrets_text
from ..tools.capability_vault import read_codex_provider_config

_HTTP_401 = re.compile(r"(?<!\d)401(?!\d)")


class BackendLoginRequired(RuntimeError):
    """A provider remained unauthorized after its sole replay."""

    phase = "backend"
    attempts = 2
    login_required = True

    def __init__(self, cause: str) -> None:
        self.cause = cause
        self.backend_error = cause
        super().__init__(
            "login_required: backend remained unauthorized after one "
            f"coordinated refresh and replay: {cause}"
        )


@dataclass(frozen=True)
class _CredentialSnapshot:
    key: tuple[str, str, str, str]
    path: Path
    env_key: str
    rejected: str = field(repr=False)


class AuthorizationRetryOwner:
    """Coordinate exactly one relay-backed replay after an agent-CLI 401."""

    def __init__(self) -> None:
        self._state_lock = threading.Lock()
        self._locks: dict[tuple[str, str, str, str], threading.Lock] = {}

    def run_agent_cli(
        self,
        backend: Any,
        *,
        prompt: str,
        resume_thread_id: str | None,
        options: Any,
        run_label: str,
    ) -> Any:
        snapshot = self._credential_snapshot(backend, options)

        def request() -> Any:
            from ..core.run_gateway import run_exec as gateway_run_exec

            return gateway_run_exec(
                backend._runner,
                prompt=prompt,
                resume_thread_id=resume_thread_id,
                options=options,
                run_label=run_label,
            )

        first = request()
        if snapshot is None or not _unauthorized_cause(first):
            return first

        lock = self._lock_for(snapshot.key)
        with lock:
            stored = _read_env_credential(snapshot.path, snapshot.env_key)
            if stored and stored != snapshot.rejected:
                os.environ[snapshot.env_key] = stored
                backend._refresh_known_secret_values()
            replay = request()

        return self._checked_replay(backend, replay)

    def _credential_snapshot(
        self,
        backend: Any,
        options: Any,
    ) -> _CredentialSnapshot | None:
        if not bool(getattr(backend, "_is_codex", False)):
            return None
        provider = read_codex_provider_config(os.environ)
        if (
            provider is None
            or provider.name != "copilot_relay"
            or provider.env_key != "COPILOT_RELAY_TOKEN"
        ):
            return None
        path = Path.home() / ".config" / "copilot-codex-relay" / "env"
        if not path.is_file():
            return None
        profile = _codex_profile(getattr(options, "extra_args", None))
        key = (
            str(path.resolve()),
            provider.name,
            provider.env_key,
            profile,
        )
        return _CredentialSnapshot(
            key=key,
            path=path,
            env_key=provider.env_key,
            rejected=str(os.environ.get(provider.env_key) or ""),
        )

    def _lock_for(self, key: tuple[str, str, str, str]) -> threading.Lock:
        with self._state_lock:
            return self._locks.setdefault(key, threading.Lock())

    @staticmethod
    def _checked_replay(backend: Any, replay: Any) -> Any:
        cause = _unauthorized_cause(replay)
        if cause:
            raise BackendLoginRequired(
                redact_secrets_text(
                    cause,
                    known_values=backend._known_secret_values,
                )
            )
        return replay


def _codex_profile(extra_args: Any) -> str:
    args = list(extra_args or [])
    for index, value in enumerate(args[:-1]):
        if value == "--profile":
            return str(args[index + 1]).strip()
    return ""


def _read_env_credential(path: Path, env_key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == env_key:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            return value
    return ""


def _unauthorized_cause(result: Any) -> str:
    failed = bool(
        int(getattr(result, "exit_code", 0) or 0) != 0
        or getattr(result, "turn_failed", False)
        or getattr(result, "fatal_error", None)
    )
    if not failed:
        return ""
    candidates = [
        getattr(result, "fatal_error", None),
        *reversed(list(getattr(result, "stderr_lines", None) or [])),
    ]
    for candidate in candidates:
        text = str(candidate or "").strip()
        if any(_HTTP_401.search(line) for line in text.splitlines()):
            return text
    return ""


AUTHORIZATION_RETRY_OWNER = AuthorizationRetryOwner()


__all__ = [
    "AUTHORIZATION_RETRY_OWNER",
    "AuthorizationRetryOwner",
    "BackendLoginRequired",
]
