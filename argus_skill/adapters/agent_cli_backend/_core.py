"""The public ``AgentCliBackend`` facade class.

Construction, small per-call state readers/setters, and the option/result
translation glue live here; the actual "make one provider call" state
machine is delegated to :mod:`._exec`.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...core.models import RunnerOptions, RunnerResult
from ...core.secret_guard import known_secret_values
from . import _exec
from ._io_log import AgentIOLogger
from ._options import (
    _compose_interrupt_providers,
    _normalize_codex_selection_args,
    _strip_legacy_codex_profile_args,
    resolve_codex_execution_model,
)
from ._result import UsageAccumulator, translate_result
from ._runtime import load_agent_cli_runtime

log = logging.getLogger(__name__)

_RUNNER_SOFT_IDLE_ENV = "ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS"
_RUNNER_STALLED_IDLE_ENV = "ARGUS_SKILL_RUNNER_STALLED_IDLE_SECONDS"
_RUNNER_HARD_IDLE_ENV = "ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS"
_RUNNER_DEFAULT_SOFT_IDLE_SECONDS = 10 * 60
_RUNNER_DEFAULT_STALLED_IDLE_SECONDS = 30 * 60
_RUNNER_DEFAULT_HARD_IDLE_SECONDS = 45 * 60


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


class AgentCliBackend:
    """``RunnerBackend`` implementation that shells out to a real CLI.

    Construct once with the runner backend choice ("codex" / "claude" /
    "copilot" / "opencode") and any cross-call defaults (e.g. ``default_extra_args``
    for ``-c "config_profile=..."``), then pass the same instance to
    every ``SkillLoop`` actor (author / engineer / reviewer). Each
    ``run_exec`` call spawns a fresh subprocess.

    Threading: the underlying ``AgentCliRunner.run_exec`` is blocking and
    not designed to be called concurrently from one instance — but
    multiple ``AgentCliBackend`` calls *are* safe in series. Use
    separate instances if you want concurrent matcher + author +
    engineer calls (the SkillLoop is sequential, so one instance is
    enough).

    Args:
        backend: which CLI to drive ("codex" / "claude" / "copilot" / "opencode").
            Defaults to the bundled runner's default (codex).
        runner_bin: explicit path to the CLI binary. Default: resolve
            from ``$PATH`` (e.g. ``codex`` / ``claude`` / ``copilot`` / ``opencode``).
        default_extra_args: appended to every command (after
            ``options.extra_args``). Useful for global ``-c`` flags.
        before_exec: called before each subprocess spawn — used to reset
            auth state etc.
        event_callback: optional ``(stream_name, line) -> None`` callback
            per stdout/stderr line. Forward this to your event sink for
            live-log streaming. argus-skill's daemon EventSink consumes
            via ``EventSink.handle_stream_line``.
    """

    def __init__(
        self,
        *,
        backend: str | None = None,
        runner_bin: str | None = None,
        default_extra_args: list[str] | None = None,
        default_interrupt_reason_provider=None,
        default_watchdog_soft_idle_seconds: int = _RUNNER_DEFAULT_SOFT_IDLE_SECONDS,
        default_watchdog_stalled_idle_seconds: int = (_RUNNER_DEFAULT_STALLED_IDLE_SECONDS),
        default_watchdog_hard_idle_seconds: int = _RUNNER_DEFAULT_HARD_IDLE_SECONDS,
        before_exec=None,
        event_callback=None,
    ) -> None:
        deps = load_agent_cli_runtime()
        self._deps = deps
        chosen = (
            deps["normalize_runner_backend"](backend)
            if backend is not None
            else deps["DEFAULT_RUNNER_BACKEND"]
        )
        self._io_logger = AgentIOLogger(external_event_callback=event_callback)
        raw_default_extra_args = list(default_extra_args or [])
        codex_backend = chosen == deps["BACKEND_CODEX"]
        normalized_default_extra_args = (
            _normalize_codex_selection_args(raw_default_extra_args)[0]
            if codex_backend
            else raw_default_extra_args
        )
        self._runner = deps["AgentCliRunner"](
            agent_bin=runner_bin,
            backend=chosen,
            event_callback=self._stream_event_callback,
            default_extra_args=normalized_default_extra_args,
            before_exec=before_exec,
        )
        self._default_extra_args = raw_default_extra_args
        self._backend_name = chosen
        self._is_codex = chosen == deps["BACKEND_CODEX"]
        self._is_copilot = chosen == deps["BACKEND_COPILOT"]
        self._default_interrupt_reason_provider = default_interrupt_reason_provider
        self._default_watchdog_soft_idle_seconds = max(
            0, int(default_watchdog_soft_idle_seconds or 0)
        )
        self._default_watchdog_stalled_idle_seconds = max(
            0, int(default_watchdog_stalled_idle_seconds or 0)
        )
        self._default_watchdog_hard_idle_seconds = max(
            0, int(default_watchdog_hard_idle_seconds or 0)
        )
        # Auth failure flag: set by run_exec() when the codex CLI
        # reports auth-related stderr. Checked by the runtime to
        # propagate to the supervisor's stop logic.
        self._auth_failure_detected: bool = False
        self._usage = UsageAccumulator()
        self._usage_context_lock = threading.Lock()
        self._usage_project_root: Path | None = None
        self._usage_global_root: Path | None = None
        self._usage_mission_id: str | None = None
        self._known_secret_values = known_secret_values()

    @property
    def backend(self) -> str:
        """Effective CLI backend after executable-availability fallback."""
        return str(self._backend_name)

    def set_acp_scope(self, scope: str) -> None:
        setter = getattr(self._runner, "set_acp_scope", None)
        if callable(setter):
            setter(scope)

    def prewarm_acp_client(
        self,
        *,
        model: str | None,
        reasoning_effort: str | None,
        lean: bool,
        cwd: str,
        front_door_session: bool = False,
        read_only: bool = False,
        add_dirs: list[str] | None = None,
    ) -> None:
        prewarm = getattr(self._runner, "prewarm_acp_client", None)
        if callable(prewarm):
            prewarm(
                model=model,
                reasoning_effort=reasoning_effort,
                lean=lean,
                cwd=cwd,
                front_door_session=front_door_session,
                read_only=read_only,
                add_dirs=add_dirs,
            )

    def close_acp_clients(self) -> None:
        close = getattr(self._runner, "close_acp_clients", None)
        if callable(close):
            close()

    def set_usage_context(
        self,
        *,
        project_root: Path | str | None,
        global_root: Path | str | None = None,
        mission_id: str | None = None,
    ) -> None:
        """Set the project/global ledgers and mission owning subsequent calls."""
        with self._usage_context_lock:
            self._usage_project_root = (
                Path(project_root).expanduser() if project_root is not None else None
            )
            self._usage_global_root = (
                Path(global_root).expanduser() if global_root is not None else None
            )
            text = str(mission_id or "").strip()
            self._usage_mission_id = text or None

    def _usage_context_snapshot(
        self,
    ) -> tuple[Path | None, str | None, Path | None]:
        with self._usage_context_lock:
            return (
                self._usage_project_root,
                self._usage_mission_id,
                self._usage_global_root,
            )

    def _configured_pricing_model(self, *, profile: str = "") -> str:
        """Read the implicit model from Codex's own config, never another route."""
        if not self._is_codex:
            return ""
        try:
            from ...tools.capability_vault import read_codex_default_model

            return read_codex_default_model(os.environ, profile=profile)
        except Exception:  # noqa: BLE001 — accounting must never break a call
            return ""

    def _resolve_execution_options(self, options: RunnerOptions) -> RunnerOptions:
        if not self._is_codex:
            return options
        normalized_call_args, _direct, _config, call_profile, call_ignore = (
            _normalize_codex_selection_args(options.extra_args)
        )
        (
            _normalized_defaults,
            _default_direct,
            _default_config,
            default_profile,
            default_ignore,
        ) = _normalize_codex_selection_args(self._default_extra_args)
        effective_profile = call_profile or default_profile
        configured_model = (
            ""
            if call_ignore or default_ignore
            else self._configured_pricing_model(
                profile=effective_profile,
            )
        )
        model = resolve_codex_execution_model(
            options.model,
            configured_model,
            self._default_extra_args,
            options.extra_args,
        )
        return replace(
            options,
            model=model or None,
            extra_args=(
                [
                    *(["--profile", effective_profile] if effective_profile else []),
                    *normalized_call_args,
                ]
                or None
            ),
        )

    # --- RunnerBackend.run_exec ------------------------------------------

    def run_exec(
        self,
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        return _exec.execute(
            self,
            prompt=prompt,
            options=options,
            run_label=run_label,
            resume_thread_id=resume_thread_id,
        )

    def _agent_io_log_path(self, options: RunnerOptions) -> Path | None:
        project_root, _mission_id, _global_root = self._usage_context_snapshot()
        if project_root is not None:
            try:
                from ...core.usage import ensure_project_events_standardized

                ensure_project_events_standardized(project_root)
            except Exception:  # noqa: BLE001 — logging must not break work
                log.exception(
                    "failed to migrate legacy project events for %s",
                    project_root,
                )
            return project_root / "events.jsonl"
        raw = os.environ.get("ARGUS_SKILL_AGENT_IO_LOG", "").strip()
        if raw:
            return Path(raw).expanduser()
        if options.working_dir:
            return Path(options.working_dir).expanduser() / ".argus" / "events.jsonl"
        return None

    def _log_agent_io(self, path: Path | None, row: dict[str, Any]) -> None:
        self._io_logger.log(path, row, known_secret_values=self._known_secret_values)

    def _close_io_context(self, call_id: str) -> None:
        self._io_logger.close(call_id)

    def _stream_event_callback(self, stream: str, line: str) -> None:
        self._io_logger.stream_event_callback(
            stream,
            line,
            backend_name=getattr(self._runner, "backend", ""),
            known_secret_values=self._known_secret_values,
        )

    # --- helpers ----------------------------------------------------------

    def _translate_options(self, options: RunnerOptions):
        cli_cls = self._deps["CliRunnerOptions"]
        # The bundled runner's RunnerOptions is a superset (has watchdog
        # hooks, add_dirs, plugin_dirs, etc.). Forward the fields
        # argus-skill exposes; the watchdog hooks are propagated when set
        # so an outer supervisor can interrupt the codex subprocess.
        interrupt_provider = _compose_interrupt_providers(
            self._default_interrupt_reason_provider,
            options.external_interrupt_reason_provider,
        )
        soft_idle = (
            self._default_watchdog_soft_idle_seconds
            if options.watchdog_soft_idle_seconds is None
            else max(0, int(options.watchdog_soft_idle_seconds))
        )
        stalled_idle = (
            self._default_watchdog_stalled_idle_seconds
            if options.watchdog_stalled_idle_seconds is None
            else max(0, int(options.watchdog_stalled_idle_seconds))
        )
        hard_idle = (
            self._default_watchdog_hard_idle_seconds
            if options.watchdog_hard_idle_seconds is None
            else max(0, int(options.watchdog_hard_idle_seconds))
        )
        option_fields = getattr(cli_cls, "__dataclass_fields__", {})
        kwargs = dict(
            model=options.model,
            reasoning_effort=options.reasoning_effort,
            dangerous_yolo=options.dangerous_yolo,
            full_auto=options.full_auto,
            skip_git_repo_check=options.skip_git_repo_check,
            extra_args=list(options.extra_args) if options.extra_args else None,
            working_dir=options.working_dir,
            output_schema_path=options.output_schema_path,
            external_interrupt_reason_provider=interrupt_provider,
            inactivity_callback=options.inactivity_callback,
            watchdog_soft_idle_seconds=soft_idle,
            watchdog_hard_idle_seconds=hard_idle,
        )
        if "watchdog_stalled_idle_seconds" in option_fields:
            kwargs["watchdog_stalled_idle_seconds"] = stalled_idle
        # Forward live_search ONLY when the target RunnerOptions supports it —
        # a test stub or an older bundled copy may not have the field; then
        # we degrade gracefully to no live search rather than crash.
        if "live_search" in option_fields:
            kwargs["live_search"] = getattr(options, "live_search", False)
        if "add_dirs" in option_fields:
            kwargs["add_dirs"] = list(options.add_dirs) if options.add_dirs else None
        if "sandbox_mode" in option_fields:
            kwargs["sandbox_mode"] = getattr(options, "sandbox_mode", None)
        if "isolate_workdir" in getattr(cli_cls, "__dataclass_fields__", {}):
            kwargs["isolate_workdir"] = getattr(options, "isolate_workdir", False)
        # Forward the live assistant-block callback the same guarded way — only
        # the Manager chat front-door sets it, and a test stub without the
        # field degrades to no streaming rather than crashing.
        if "on_agent_message" in getattr(cli_cls, "__dataclass_fields__", {}):
            kwargs["on_agent_message"] = getattr(options, "on_agent_message", None)
        return cli_cls(**kwargs)

    def _translate_result(
        self,
        cli_result,
        *,
        resume_thread_id: str | None = None,
        copilot_usage=None,
    ) -> RunnerResult:
        return translate_result(
            cli_result,
            resume_thread_id=resume_thread_id,
            copilot_usage=copilot_usage,
            usage_accumulator=self._usage,
        )

    def _usage_delta_for_thread(
        self,
        *,
        thread_id: str | None,
        raw_totals: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        """Convert Codex lifecycle-cumulative usage into this call's delta."""
        return self._usage.usage_delta_for_thread(
            thread_id=thread_id,
            raw_totals=raw_totals,
        )

    def _premium_delta_for_thread(
        self,
        *,
        thread_id: str | None,
        raw_total: float,
        resume_baseline_unknown: bool = False,
    ) -> float | None:
        """Convert copilot's session-cumulative premiumRequests into this
        call's delta. Mirrors ``_usage_delta_for_thread`` otherwise.
        """
        return self._usage.premium_delta_for_thread(
            thread_id=thread_id,
            raw_total=raw_total,
            resume_baseline_unknown=resume_baseline_unknown,
        )


# --- Convenience factory ---------------------------------------------------


def build_agent_cli_backend_from_env() -> AgentCliBackend:
    """Build a AgentCliBackend from environment variables.

    Honours:

      * ``ARGUS_SKILL_RUNNER_BACKEND`` — "codex" / "claude" / "copilot" / "opencode"
        (default: codex)
      * ``ARGUS_SKILL_RUNNER_BIN``     — path to the CLI binary
      * ``ARGUS_SKILL_RUNNER_EXTRA_ARGS`` — space-separated default args
        appended to every command (use shell-style quoting at your own
        risk; we use ``shlex.split``).
      * ``ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS`` — no-event diagnostic warning,
        default 600 seconds.
      * ``ARGUS_SKILL_RUNNER_STALLED_IDLE_SECONDS`` — likely-stalled warning,
        default 1800 seconds.
      * ``ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS`` — terminate only the current
        model process group, default 2700 seconds. Set any threshold to ``0`` to
        disable that stage explicitly.
    """
    import shlex

    backend = os.environ.get("ARGUS_SKILL_RUNNER_BACKEND") or None
    from ...core.knobs import resolve_runner_bin_setting

    runner_bin = resolve_runner_bin_setting() or None
    raw_extra = os.environ.get("ARGUS_SKILL_RUNNER_EXTRA_ARGS", "").strip()
    extra = _strip_legacy_codex_profile_args(shlex.split(raw_extra) if raw_extra else None)
    return AgentCliBackend(
        backend=backend,
        runner_bin=runner_bin,
        default_extra_args=extra,
        default_watchdog_soft_idle_seconds=_env_int(
            _RUNNER_SOFT_IDLE_ENV,
            _RUNNER_DEFAULT_SOFT_IDLE_SECONDS,
        ),
        default_watchdog_stalled_idle_seconds=_env_int(
            _RUNNER_STALLED_IDLE_ENV,
            _RUNNER_DEFAULT_STALLED_IDLE_SECONDS,
        ),
        default_watchdog_hard_idle_seconds=_env_int(
            _RUNNER_HARD_IDLE_ENV,
            _RUNNER_DEFAULT_HARD_IDLE_SECONDS,
        ),
    )
