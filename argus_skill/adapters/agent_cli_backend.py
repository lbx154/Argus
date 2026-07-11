"""Real LLM backend: thin adapter over ArgusBot's ``AgentCliRunner``.

argus-skill's loop is deliberately backend-agnostic — it talks to a
``RunnerBackend`` (Protocol) defined in ``argus_skill.core.ports``. The
deterministic ``MemoryBackend`` is fine for tests, but for *real* runs
we need to drive the actual codex / claude / copilot CLI.

ArgusBot already ships a battle-tested subprocess wrapper —
``agent_cli.agent_cli_runner.AgentCliRunner`` — that handles JSON event
streams, idle watchdogs, claude/copilot dialects, and cross-platform
stdin quirks. Re-vendoring would mean carrying ~700 LOC + tests of
edge-case bug fixes. So instead this adapter *wraps* it.

Provenance: new code. Depends on ArgusBot being importable
(``pip install 'argus-skill[codex]'``).

The translation layer:

  argus-skill's ``RunnerOptions``   →   ArgusBot's ``RunnerOptions``
  argus-skill's ``run_label`` kwarg →   ArgusBot's ``run_label``
  ArgusBot's   ``AgentRunResult``   →   argus-skill's ``RunnerResult``

Field names are mostly 1:1 (both projects evolved from the same
ancestor); we keep only the slim subset argus-skill needs.

Token usage is best-effort — codex's JSON event stream emits
``token_count.input_tokens`` / ``output_tokens`` in some events; we
sum them across the run when present. When unavailable we leave them
at 0 (the loop never branches on token counts).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..core.codex_usage import (
    TokenUsage,
    extract_token_usage,
    sum_token_counts,
)
from ..core.copilot_usage import (
    CopilotCallUsage,
    capture_copilot_usage_cursor,
    read_copilot_usage_since,
)
from ..core.models import RunnerOptions, RunnerResult

log = logging.getLogger(__name__)


def _sum_token_counts(
    events: list[dict[str, Any]] | None,
) -> tuple[int, int, int, int]:
    """Backward-compatible adapter export for existing callers/tests."""
    return sum_token_counts(events)


_AUTH_FAILURE_PATTERNS: tuple[str, ...] = (
    "unauthorized",
    "expired token",
    "invalid token",
    "authentication failed",
    "access denied by policy settings",
    "subscription does not include this feature",
    "required policies have not been enabled",
    "401",
    "403",
    "please run `codex login`",
    "codex login",
    "invalid api key",
    "no api key",
    "missing credentials",
)
_RUNNER_SOFT_IDLE_ENV = "ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS"
_RUNNER_HARD_IDLE_ENV = "ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS"
_RUNNER_DEFAULT_SOFT_IDLE_SECONDS = 0
_RUNNER_DEFAULT_HARD_IDLE_SECONDS = 60 * 60
_RECOVERABLE_RECONNECT_RE = re.compile(r"^reconnecting\.\.\.\s*(\d+)/(\d+)\b")
_LEGACY_CODEX_PROFILE_SWITCHES = {"-c", "--config"}
_LEGACY_CODEX_PROFILE_PAYLOADS = {"profile=auto-max", "config_profile=auto-max"}
_AGENT_IO_LOG_ENV = "ARGUS_SKILL_AGENT_IO_LOG"
_COMPACT_IO_RUN_LABELS = frozenset({
    "skill.compaction_batch",
    "wiki.compaction_batch",
})


def _compact_agent_io(run_label: str) -> bool:
    return (run_label or "").strip().lower() in _COMPACT_IO_RUN_LABELS


def looks_like_auth_failure(stderr_lines) -> bool:  # noqa: ANN001
    """Return True iff any stderr line matches a known auth-failure pattern.

    Used by the lifetime daemon to detect "codex token expired overnight"
    without crashing — the daemon logs a warning, finishes the current
    mission as failed, and keeps polling. Operators see the warning in
    the journal / stderr and re-authenticate at their leisure.
    """
    if not stderr_lines:
        return False
    for raw in stderr_lines:
        if not raw:
            continue
        low = str(raw).lower()
        for pat in _AUTH_FAILURE_PATTERNS:
            if pat in low:
                return True
    return False


def _interrupt_reason(provider: Any) -> str:
    if provider is None:
        return ""
    try:
        return str(provider() or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _normalize_codex_config_arg(arg: str) -> str:
    return re.sub(r"\s+", "", str(arg)).replace('"', "").replace("'", "").casefold()


def _is_legacy_codex_profile_arg(arg: str) -> bool:
    return _normalize_codex_config_arg(arg) in _LEGACY_CODEX_PROFILE_PAYLOADS


def _strip_legacy_codex_profile_args(
    extra_args: list[str] | None,
) -> list[str] | None:
    """Remove obsolete auto-max profile flags that break matcher startup.

    The old launcher path forwarded ``-c profile = "auto-max"`` into the
    Codex CLI. Current matching runs do not need that profile, and the legacy
    flag now trips a config parse failure before the skill matcher can start.
    Keep other extra args intact so explicit runner overrides still work.
    """
    if not extra_args:
        return None
    cleaned: list[str] = []
    removed = False
    i = 0
    while i < len(extra_args):
        arg = extra_args[i]
        if arg in _LEGACY_CODEX_PROFILE_SWITCHES and i + 1 < len(extra_args):
            next_arg = extra_args[i + 1]
            if _is_legacy_codex_profile_arg(next_arg):
                removed = True
                log.warning(
                    "stripping legacy Codex auto-max profile args from runner config"
                )
                i += 2
                continue
        if _is_legacy_codex_profile_arg(arg):
            removed = True
            log.warning(
                "stripping legacy Codex auto-max profile arg from runner config"
            )
            i += 1
            continue
        cleaned.append(arg)
        i += 1
    if removed:
        return cleaned or None
    return list(extra_args)


def _jsonl_append(path: Path, row: dict[str, Any], lock: threading.Lock) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    except Exception:  # noqa: BLE001
        return
    try:
        with lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except OSError:
        return


# --- ArgusBot import (lazy, with friendly error) ---------------------------

def _import_argusbot():
    """Resolve the codex/claude/copilot CLI runner shipped with argus-skill.

    Prefers the vendored copy under ``argus_skill.agent_cli`` (no
    separate install step). Falls back to a top-level ``agent_cli``
    package if one is on ``sys.path`` — this preserves compatibility with
    historical ``pip install 'argus-skill[codex]'`` installs and with local
    upstream ArgusBot checkouts used for development.
    """
    try:
        from argus_skill.agent_cli.agent_cli_runner import (
            AgentCliRunner,
        )
        from argus_skill.agent_cli.agent_cli_runner import (
            RunnerOptions as ArgusRunnerOptions,
        )
        from argus_skill.agent_cli.runner_backend import (
            BACKEND_CLAUDE,
            BACKEND_CODEX,
            BACKEND_COPILOT,
            DEFAULT_RUNNER_BACKEND,
            default_runner_bin,
            normalize_runner_backend,
        )
    except ImportError:
        try:
            from agent_cli.agent_cli_runner import (
                AgentCliRunner,
            )
            from agent_cli.agent_cli_runner import (
                RunnerOptions as ArgusRunnerOptions,
            )
            from agent_cli.runner_backend import (
                BACKEND_CLAUDE,
                BACKEND_CODEX,
                BACKEND_COPILOT,
                DEFAULT_RUNNER_BACKEND,
                default_runner_bin,
                normalize_runner_backend,
            )
        except ImportError as exc:  # pragma: no cover - environmental
            raise ImportError(
                "AgentCliBackend requires the bundled agent_cli "
                "module. Reinstall argus-skill, or add a sibling "
                "`agent_cli` package to PYTHONPATH for development."
            ) from exc
    return {
        "AgentCliRunner": AgentCliRunner,
        "ArgusRunnerOptions": ArgusRunnerOptions,
        "BACKEND_CLAUDE": BACKEND_CLAUDE,
        "BACKEND_CODEX": BACKEND_CODEX,
        "BACKEND_COPILOT": BACKEND_COPILOT,
        "DEFAULT_RUNNER_BACKEND": DEFAULT_RUNNER_BACKEND,
        "default_runner_bin": default_runner_bin,
        "normalize_runner_backend": normalize_runner_backend,
    }


# --- The adapter -----------------------------------------------------------


class AgentCliBackend:
    """``RunnerBackend`` implementation that shells out to a real CLI.

    Construct once with the runner backend choice ("codex" / "claude" /
    "copilot") and any cross-call defaults (e.g. ``default_extra_args``
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
        backend: which CLI to drive ("codex" / "claude" / "copilot").
            Defaults to ArgusBot's default (codex).
        runner_bin: explicit path to the CLI binary. Default: resolve
            from ``$PATH`` (e.g. ``codex`` / ``claude`` / ``copilot``).
        default_extra_args: appended to every command (after
            ``options.extra_args``). Useful for global ``-c`` flags.
        before_exec: called before each subprocess spawn. ArgusBot uses
            this to reset auth state etc.
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
        default_watchdog_soft_idle_seconds: int = 0,
        default_watchdog_hard_idle_seconds: int = 0,
        before_exec=None,
        event_callback=None,
    ) -> None:
        deps = _import_argusbot()
        self._deps = deps
        chosen = (
            deps["normalize_runner_backend"](backend)
            if backend is not None
            else deps["DEFAULT_RUNNER_BACKEND"]
        )
        self._external_event_callback = event_callback
        self._io_log_lock = threading.Lock()
        self._io_context = threading.local()
        self._argus_runner = deps["AgentCliRunner"](
            agent_bin=runner_bin,
            backend=chosen,
            event_callback=self._stream_event_callback,
            default_extra_args=default_extra_args,
            before_exec=before_exec,
        )
        self._backend_name = chosen
        self._is_codex = chosen == deps["BACKEND_CODEX"]
        self._is_copilot = chosen == deps["BACKEND_COPILOT"]
        self._default_interrupt_reason_provider = default_interrupt_reason_provider
        # SOURCE-LEVEL per-mission budget cap. A live provider set per-mission
        # (see ``set_budget_reason_provider``): it returns a non-empty reason once
        # the mission's spend hits its cap, which is composed into the interrupt
        # chain above so ``AgentCliRunner.run_exec`` refuses to spawn a NEW LLM
        # call — enforcing the cap at the finest granularity (no round can
        # overspend past it before the between-rounds breaker checks). ``None`` =
        # no cap (default; every existing caller unchanged).
        self._budget_reason_provider = None
        self._default_watchdog_soft_idle_seconds = max(
            0, int(default_watchdog_soft_idle_seconds or 0)
        )
        self._default_watchdog_hard_idle_seconds = max(
            0, int(default_watchdog_hard_idle_seconds or 0)
        )
        # Auth failure flag: set by run_exec() when the codex CLI
        # reports auth-related stderr. Checked by the REPL runner to
        # propagate to the supervisor's stop logic.
        self._auth_failure_detected: bool = False
        self._usage_lock = threading.Lock()
        self._thread_usage_totals: dict[str, tuple[int, int, int, int]] = {}
        # Copilot reports premiumRequests as a session-cumulative total; keep the
        # last-seen total per thread to charge each call only its delta.
        # copilot 的 premiumRequests 是会话累计值；按线程存上次累计，只计本次增量。
        self._thread_premium_totals: dict[str, float] = {}
        self._usage_context_lock = threading.Lock()
        self._usage_project_root: Path | None = None
        self._usage_mission_id: str | None = None

    def set_budget_reason_provider(self, provider) -> None:
        """Install (or clear with ``None``) the per-mission budget guard.

        ``provider() -> str | None`` is polled live: a non-empty string means the
        mission has hit its cap. It is composed into the interrupt chain so a new
        LLM call through this backend is refused at the source once the cap trips.
        The mission entry (``_SkillLoopRunner.execute``) sets this for the mission
        and clears it in a ``finally`` so it never leaks to a later mission."""
        self._budget_reason_provider = provider

    def set_usage_context(
        self,
        *,
        project_root: Path | str | None,
        mission_id: str | None = None,
    ) -> None:
        """Set the project ledger and optional mission owning subsequent calls."""
        with self._usage_context_lock:
            self._usage_project_root = (
                Path(project_root).expanduser() if project_root is not None else None
            )
            text = str(mission_id or "").strip()
            self._usage_mission_id = text or None

    def _usage_context_snapshot(self) -> tuple[Path | None, str | None]:
        with self._usage_context_lock:
            return self._usage_project_root, self._usage_mission_id

    # --- RunnerBackend.run_exec ------------------------------------------

    def run_exec(
        self,
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        # Reset per-call: the flag is checked AFTER this call completes,
        # so stale True from a previous call cannot stick across missions.
        self._auth_failure_detected = False
        argus_options = self._translate_options(options)
        call_id = uuid.uuid4().hex
        started_at = time.time()
        log_path = self._agent_io_log_path(options)
        self._io_context.current = {
            "call_id": call_id,
            "run_label": run_label,
            "log_path": str(log_path) if log_path is not None else "",
            "model": options.model,
            "compact_io": _compact_agent_io(run_label),
        }

        def _finalize_result(
            result: RunnerResult,
            *,
            status: str,
            token_usage: TokenUsage | None = None,
            premium_requests: float | None = None,
            error: str = "",
        ) -> RunnerResult:
            completed_at = time.time()
            result.call_id = call_id
            result.thread_id = result.thread_id or resume_thread_id
            result.started_at = started_at
            result.completed_at = completed_at
            result.duration_ms = max(
                0,
                int(round((completed_at - started_at) * 1000)),
            )
            usage = token_usage or TokenUsage(
                input_tokens=result.input_tokens,
                cached_input_tokens=result.cached_input_tokens,
                cache_write_tokens=result.cache_write_tokens,
                output_tokens=result.output_tokens,
                reasoning_output_tokens=result.reasoning_output_tokens,
                input_tokens_present=result.input_tokens_present,
                cached_input_tokens_present=result.cached_input_tokens_present,
                cache_write_tokens_present=result.cache_write_tokens_present,
                output_tokens_present=result.output_tokens_present,
                reasoning_output_tokens_present=(
                    result.reasoning_output_tokens_present
                ),
                source="result",
            )
            premium = (
                premium_requests
                if premium_requests is not None
                else (
                    result.premium_requests
                    if result.premium_requests_present
                    else None
                )
            )
            project_root, mission_id = self._usage_context_snapshot()
            if project_root is None and log_path is not None:
                project_root = log_path.parent
            if project_root is not None:
                try:
                    from ..core.usage import (
                        UsageLedger,
                        build_usage_record,
                        usage_recorded_event,
                    )

                    record = build_usage_record(
                        call_id=call_id,
                        project_root=project_root,
                        mission_id=mission_id,
                        provider=self._backend_name,
                        model=result.usage_model or str(options.model or ""),
                        run_label=run_label,
                        started_at=started_at,
                        completed_at=completed_at,
                        status=(
                            status
                            if status in {"completed", "error", "denied"}
                            else "error"
                        ),
                        token_usage=usage,
                        premium_requests=premium,
                        total_nano_aiu=result.total_nano_aiu,
                        thread_id=result.thread_id,
                        model_usage=result.model_usage,
                        error=error or str(result.fatal_error or ""),
                    )
                    appended = UsageLedger(
                        project_root,
                        migrate_legacy=False,
                    ).append(record)
                    result.pricing_status = record.pricing_status
                    result.cost_usd = record.cost_usd
                    if appended:
                        self._log_agent_io(log_path, usage_recorded_event(record))
                except Exception:  # noqa: BLE001 — accounting must not break work
                    log.exception("failed to persist usage record for %s", call_id)
            self._io_context.current = None
            return result

        copilot_permit = None
        codex_permit = None
        codex_quota_active = False
        if self._is_codex:
            from ..core.provider_quota import codex_quota_enabled

            codex_quota_active = codex_quota_enabled()
        interrupted = (
            _interrupt_reason(
                getattr(argus_options, "external_interrupt_reason_provider", None)
            )
            if self._is_copilot or codex_quota_active
            else None
        )
        if self._is_copilot and not interrupted:
            from ..core.copilot_guard import (
                acquire_copilot_permit,
                release_denied_permit,
            )

            copilot_permit = acquire_copilot_permit(run_label)
            if not copilot_permit.allowed:
                reason = copilot_permit.reason
                release_denied_permit(copilot_permit)
                self._log_agent_io(log_path, {
                    "type": "provider.request.denied",
                    "provider": "copilot",
                    "call_id": call_id,
                    "run_label": run_label,
                    "reason": reason,
                    "ts": time.time(),
                })
                log.warning("Copilot call blocked before start (%s): %s", run_label, reason)
                return _finalize_result(
                    RunnerResult(
                        exit_code=-1,
                        thread_id=resume_thread_id,
                        fatal_error=f"refused before start: {reason}",
                    ),
                    status="denied",
                    error=reason,
                )
        elif self._is_codex and not interrupted:
            from ..core.provider_quota import acquire_codex_permit

            codex_permit = acquire_codex_permit(run_label)
            if not codex_permit.allowed:
                reason = codex_permit.reason
                self._log_agent_io(log_path, {
                    "type": "provider.request.denied",
                    "provider": "codex",
                    "call_id": call_id,
                    "run_label": run_label,
                    "reason": reason,
                    "daily_calls": codex_permit.daily_calls,
                    "daily_cap": codex_permit.daily_cap,
                    "ts": time.time(),
                })
                log.warning("Codex call blocked before start (%s): %s", run_label, reason)
                return _finalize_result(
                    RunnerResult(
                        exit_code=-1,
                        thread_id=resume_thread_id,
                        fatal_error=f"refused before start: {reason}",
                    ),
                    status="denied",
                    error=reason,
                )

        quota_permit = copilot_permit or codex_permit
        event_permit = (
            quota_permit
            if quota_permit is not None and bool(getattr(quota_permit, "guarded", True))
            else None
        )
        if event_permit is not None:
            self._log_agent_io(log_path, {
                "type": "provider.request.started",
                "provider": self._backend_name,
                "call_id": call_id,
                "run_label": run_label,
                "daily_calls": int(getattr(event_permit, "daily_calls", 0) or 0),
                "daily_cap": int(getattr(event_permit, "daily_cap", 0) or 0),
                "premium_requests_today": float(
                    getattr(event_permit, "premium_requests_today", 0.0) or 0.0
                ),
                "premium_cap": float(getattr(event_permit, "premium_cap", 0.0) or 0.0),
                "ts": time.time(),
            })

        def _finish_quota(
            *,
            success: bool,
            error_text: str = "",
            premium_requests: float = 0.0,
        ) -> None:
            if copilot_permit is not None:
                copilot_permit.finish(
                    premium_requests=premium_requests,
                    error_text=error_text,
                    success=success,
                )
            if codex_permit is not None:
                codex_permit.finish(success=success, error_text=error_text)
            if event_permit is not None:
                self._log_agent_io(log_path, {
                    "type": "provider.request.completed",
                    "provider": self._backend_name,
                    "call_id": call_id,
                    "run_label": run_label,
                    "success": bool(success),
                    "error": (error_text or "")[:500],
                    "daily_calls": int(getattr(event_permit, "daily_calls", 0) or 0),
                    "daily_cap": int(getattr(event_permit, "daily_cap", 0) or 0),
                    "premium_requests": float(premium_requests or 0.0),
                    "ts": time.time(),
                })

        start_row: dict[str, Any] = {
            "type": "agent.io.start",
            "io_kind": "start",
            "call_id": call_id,
            "run_label": run_label,
            "backend": self._argus_runner.backend,
            "model": options.model,
            "reasoning_effort": options.reasoning_effort,
            "working_dir": options.working_dir,
            "resume_thread_id": resume_thread_id,
            "ts": time.time(),
        }
        if _compact_agent_io(run_label):
            start_row["prompt_chars"] = len(prompt)
        else:
            start_row["prompt"] = prompt
        self._log_agent_io(log_path, start_row)
        copilot_usage_cursor = (
            capture_copilot_usage_cursor() if self._is_copilot else None
        )
        try:
            argus_result = self._argus_runner.run_exec(
                prompt=prompt,
                resume_thread_id=resume_thread_id,
                options=argus_options,
                run_label=run_label,
            )
        except FileNotFoundError as exc:
            log.exception("codex CLI binary not found")
            _finish_quota(error_text=str(exc), success=False)
            self._log_agent_io(log_path, {
                "type": "agent.io.error",
                "io_kind": "error",
                "call_id": call_id,
                "run_label": run_label,
                "backend": getattr(self._argus_runner, "backend", ""),
                "error": f"runner binary not found: {exc}",
                "ts": time.time(),
            })
            return _finalize_result(
                RunnerResult(
                    exit_code=127,
                    fatal_error=f"runner binary not found: {exc}",
                ),
                status="denied",
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 — last-line safety net
            log.exception("codex runner raised")
            _finish_quota(
                error_text=f"{type(exc).__name__}: {exc}",
                success=False,
            )
            self._log_agent_io(log_path, {
                "type": "agent.io.error",
                "io_kind": "error",
                "call_id": call_id,
                "run_label": run_label,
                "backend": getattr(self._argus_runner, "backend", ""),
                "error": f"{type(exc).__name__}: {exc}",
                "ts": time.time(),
            })
            return _finalize_result(
                RunnerResult(
                    exit_code=-1,
                    fatal_error=f"{type(exc).__name__}: {exc}",
                ),
                status="error",
                error=f"{type(exc).__name__}: {exc}",
            )

        copilot_usage = read_copilot_usage_since(
            copilot_usage_cursor,
            session_id=(
                getattr(argus_result, "thread_id", None) or resume_thread_id
            ),
        )
        try:
            translated = self._translate_result(
                argus_result,
                resume_thread_id=resume_thread_id,
                copilot_usage=copilot_usage,
            )
        except Exception as exc:  # noqa: BLE001
            _finish_quota(
                error_text=f"result translation failed: {exc}",
                success=False,
            )
            raw_usage = extract_token_usage(
                getattr(argus_result, "json_events", None)
            )
            raw_premium, raw_premium_present = _extract_copilot_premium_requests(
                getattr(argus_result, "json_events", None)
            )
            return _finalize_result(
                RunnerResult(
                    exit_code=-1,
                    thread_id=(
                        getattr(argus_result, "thread_id", None)
                        or resume_thread_id
                    ),
                    fatal_error=f"result translation failed: {exc}",
                    usage_model=(
                        copilot_usage.model if copilot_usage is not None else ""
                    ),
                    total_nano_aiu=(
                        copilot_usage.total_nano_aiu
                        if copilot_usage is not None
                        else None
                    ),
                    model_usage=(
                        list(copilot_usage.model_usage)
                        if copilot_usage is not None
                        else []
                    ),
                ),
                status="error",
                token_usage=raw_usage,
                premium_requests=raw_premium if raw_premium_present else None,
                error=f"result translation failed: {exc}",
            )

        failed = bool(
            getattr(argus_result, "turn_failed", False)
            or getattr(argus_result, "fatal_error", None)
            or int(getattr(argus_result, "exit_code", 0) or 0) != 0
        )
        stderr_lines = list(getattr(argus_result, "stderr_lines", None) or [])
        fatal_error = str(getattr(argus_result, "fatal_error", "") or "")
        failure_text = "\n".join([fatal_error, *map(str, stderr_lines)]).strip()

        # Detect auth/policy failures even when Copilot exits 0 but reports
        # turn_failed=true. Policy denial previously looked "successful" at the
        # process level, so every daemon kept retrying a blocked account.
        if failed and looks_like_auth_failure([failure_text]):
            self._auth_failure_detected = True
            log.warning(
                "agent backend reported auth/policy failure "
                "(run_label=%s, exit_code=%d)",
                run_label,
                int(getattr(argus_result, "exit_code", 0) or 0),
            )

        _finish_quota(
            premium_requests=translated.premium_requests,
            error_text=failure_text,
            success=not failed,
        )

        complete_row: dict[str, Any] = {
            "type": "agent.io.complete",
            "io_kind": "complete",
            "call_id": call_id,
            "run_label": run_label,
            "backend": getattr(self._argus_runner, "backend", ""),
            "model": translated.usage_model or options.model,
            "exit_code": getattr(argus_result, "exit_code", None),
            "thread_id": getattr(argus_result, "thread_id", None),
            "turn_completed": getattr(argus_result, "turn_completed", None),
            "turn_failed": getattr(argus_result, "turn_failed", None),
            "fatal_error": getattr(argus_result, "fatal_error", None),
            "input_tokens": translated.input_tokens,
            "cached_input_tokens": translated.cached_input_tokens,
            "cache_write_tokens": translated.cache_write_tokens,
            "output_tokens": translated.output_tokens,
            "reasoning_output_tokens": translated.reasoning_output_tokens,
            "premium_requests": translated.premium_requests,
            "total_nano_aiu": translated.total_nano_aiu,
            "usage_model": translated.usage_model,
            "ts": time.time(),
        }
        if _compact_agent_io(run_label):
            complete_row.update({
                "agent_message_count": len(getattr(argus_result, "agent_messages", []) or []),
                "stdout_line_count": len(getattr(argus_result, "stdout_lines", []) or []),
                "stderr_line_count": len(getattr(argus_result, "stderr_lines", []) or []),
                "json_event_count": len(getattr(argus_result, "json_events", []) or []),
            })
        else:
            complete_row.update({
                "command": list(getattr(argus_result, "command", []) or []),
                "agent_messages": list(getattr(argus_result, "agent_messages", []) or []),
                "stdout_lines": list(getattr(argus_result, "stdout_lines", []) or []),
                "stderr_lines": list(getattr(argus_result, "stderr_lines", []) or []),
                "json_events": list(getattr(argus_result, "json_events", []) or []),
            })
        self._log_agent_io(log_path, complete_row)
        return _finalize_result(
            translated,
            status="error" if failed else "completed",
            error=failure_text,
        )

    def _agent_io_log_path(self, options: RunnerOptions) -> Path | None:
        project_root, _mission_id = self._usage_context_snapshot()
        if project_root is not None:
            try:
                from ..core.usage import ensure_project_events_standardized

                ensure_project_events_standardized(project_root)
            except Exception:  # noqa: BLE001 — logging must not break work
                log.exception(
                    "failed to migrate legacy project events for %s",
                    project_root,
                )
            return project_root / "events.jsonl"
        raw = os.environ.get(_AGENT_IO_LOG_ENV, "").strip()
        if raw:
            return Path(raw).expanduser()
        if options.working_dir:
            return Path(options.working_dir).expanduser() / ".argus" / "events.jsonl"
        return None

    def _log_agent_io(self, path: Path | None, row: dict[str, Any]) -> None:
        if path is None:
            return
        _jsonl_append(path, row, self._io_log_lock)

    def _stream_event_callback(self, stream: str, line: str) -> None:
        ctx = getattr(self._io_context, "current", None) or {}
        log_path = str(ctx.get("log_path") or "")
        if log_path and not bool(ctx.get("compact_io")):
            self._log_agent_io(Path(log_path), {
                "type": "agent.io.stream",
                "io_kind": "stream",
                "call_id": ctx.get("call_id"),
                "run_label": ctx.get("run_label"),
                "backend": getattr(self._argus_runner, "backend", ""),
                "model": ctx.get("model"),
                "stream": stream,
                "line": line,
                "ts": time.time(),
            })
        if self._external_event_callback is not None:
            self._external_event_callback(stream, line)

    # --- helpers ----------------------------------------------------------

    def _translate_options(self, options: RunnerOptions):
        argus_cls = self._deps["ArgusRunnerOptions"]
        # ArgusBot's RunnerOptions is a superset (has watchdog hooks,
        # add_dirs, plugin_dirs, etc.). Forward the fields argus-skill
        # exposes; the watchdog hooks are propagated when set so an
        # outer supervisor can interrupt the codex subprocess.
        interrupt_provider = _compose_interrupt_providers(
            self._default_interrupt_reason_provider,
            self._budget_reason_provider,
            options.external_interrupt_reason_provider,
        )
        soft_idle = (
            options.watchdog_soft_idle_seconds
            or self._default_watchdog_soft_idle_seconds
        )
        hard_idle = (
            options.watchdog_hard_idle_seconds
            or self._default_watchdog_hard_idle_seconds
        )
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
        # Forward live_search ONLY when the target RunnerOptions supports it — an
        # older external/vendored ArgusBot copy (or a test stub) may not have the
        # field; then we degrade gracefully to no live search rather than crash.
        if "live_search" in getattr(argus_cls, "__dataclass_fields__", {}):
            kwargs["live_search"] = getattr(options, "live_search", False)
        # Forward the live assistant-block callback the same guarded way — only
        # the Manager chat front-door sets it, and a vendored copy without the
        # field degrades to no streaming rather than crashing.
        if "on_agent_message" in getattr(argus_cls, "__dataclass_fields__", {}):
            kwargs["on_agent_message"] = getattr(options, "on_agent_message", None)
        return argus_cls(**kwargs)

    def _translate_result(
        self,
        argus_result,
        *,
        resume_thread_id: str | None = None,
        copilot_usage: CopilotCallUsage | None = None,
    ) -> RunnerResult:
        if copilot_usage is not None:
            raw_usage = TokenUsage(
                input_tokens=copilot_usage.input_tokens or 0,
                cached_input_tokens=copilot_usage.cache_read_tokens or 0,
                cache_write_tokens=copilot_usage.cache_write_tokens or 0,
                output_tokens=copilot_usage.output_tokens or 0,
                reasoning_output_tokens=copilot_usage.reasoning_tokens or 0,
                input_tokens_present=copilot_usage.input_tokens is not None,
                cached_input_tokens_present=(
                    copilot_usage.cache_read_tokens is not None
                ),
                cache_write_tokens_present=(
                    copilot_usage.cache_write_tokens is not None
                ),
                output_tokens_present=copilot_usage.output_tokens is not None,
                reasoning_output_tokens_present=(
                    copilot_usage.reasoning_tokens is not None
                ),
                source="copilot_session_store",
            )
            (
                input_tokens,
                cached_input_tokens,
                output_tokens,
                reasoning_output_tokens,
            ) = raw_usage.as_tuple()
        else:
            raw_usage = extract_token_usage(
                getattr(argus_result, "json_events", None)
            )
            (
                input_tokens,
                cached_input_tokens,
                output_tokens,
                reasoning_output_tokens,
            ) = self._usage_delta_for_thread(
                thread_id=argus_result.thread_id or resume_thread_id,
                raw_totals=raw_usage.as_tuple(),
            )
        raw_premium, premium_requests_present = _extract_copilot_premium_requests(
            getattr(argus_result, "json_events", None)
        )
        premium_requests = self._premium_delta_for_thread(
            thread_id=argus_result.thread_id or resume_thread_id,
            raw_total=raw_premium,
        )
        return RunnerResult(
            exit_code=argus_result.exit_code,
            agent_messages=list(argus_result.agent_messages or []),
            stdout_lines=list(argus_result.stdout_lines or []),
            stderr_lines=list(argus_result.stderr_lines or []),
            thread_id=argus_result.thread_id or resume_thread_id,
            fatal_error=_normalize_fatal_error(argus_result.fatal_error),
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_tokens=raw_usage.cache_write_tokens,
            output_tokens=output_tokens,
            reasoning_output_tokens=reasoning_output_tokens,
            premium_requests=premium_requests,
            input_tokens_present=raw_usage.input_tokens_present,
            cached_input_tokens_present=raw_usage.cached_input_tokens_present,
            cache_write_tokens_present=raw_usage.cache_write_tokens_present,
            output_tokens_present=raw_usage.output_tokens_present,
            reasoning_output_tokens_present=(
                raw_usage.reasoning_output_tokens_present
            ),
            premium_requests_present=premium_requests_present,
            usage_model=copilot_usage.model if copilot_usage is not None else "",
            total_nano_aiu=(
                copilot_usage.total_nano_aiu
                if copilot_usage is not None
                else None
            ),
            model_usage=(
                list(copilot_usage.model_usage)
                if copilot_usage is not None
                else []
            ),
        )

    def _usage_delta_for_thread(
        self,
        *,
        thread_id: str | None,
        raw_totals: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        """Convert Codex lifecycle-cumulative usage into this call's delta."""
        if not thread_id:
            return raw_totals

        with self._usage_lock:
            previous = self._thread_usage_totals.get(thread_id)
            self._thread_usage_totals[thread_id] = raw_totals

        if previous is None:
            return raw_totals

        deltas = (
            raw_totals[0] - previous[0],
            raw_totals[1] - previous[1],
            raw_totals[2] - previous[2],
            raw_totals[3] - previous[3],
        )
        if any(delta < 0 for delta in deltas):
            log.debug(
                "codex usage totals decreased; treating current total as fresh delta "
                "(thread_id=%s, previous=%s, current=%s)",
                thread_id,
                previous,
                raw_totals,
            )
            return raw_totals
        return deltas

    def _premium_delta_for_thread(
        self,
        *,
        thread_id: str | None,
        raw_total: float,
    ) -> float:
        """Convert copilot's session-cumulative premiumRequests into this call's
        delta. Mirrors ``_usage_delta_for_thread`` for the scalar case.
        把 copilot 会话累计的 premiumRequests 转成本次调用的增量（标量版）。"""
        if raw_total <= 0.0:
            return 0.0
        if not thread_id:
            return raw_total

        with self._usage_lock:
            previous = self._thread_premium_totals.get(thread_id)
            self._thread_premium_totals[thread_id] = raw_total

        if previous is None:
            return raw_total
        delta = raw_total - previous
        if delta < 0.0:
            # Cumulative counter reset (new session on the same id) — charge the
            # current total as a fresh delta rather than a negative credit.
            return raw_total
        return delta

def _sum_copilot_premium_requests(events: list[dict[str, Any]] | None) -> float:
    """Best-effort copilot premium-request total from its JSON event stream.

    EN: The copilot CLI ends each turn with a ``result`` event carrying
    ``usage.premiumRequests`` — a SESSION-CUMULATIVE running total (turn 1: 7.5,
    after a resumed turn: 15, …), NOT a per-turn delta. We return the LAST such
    total seen; the backend adapter de-cumulates it into this call's delta
    per-thread (mirroring how codex token totals are handled). codex/claude
    emit no such field → 0.0.
    中文：copilot CLI 每轮以 ``result`` 事件收尾，带 ``usage.premiumRequests``——这是
    「会话累计」总数（第 1 轮 7.5，续接后 15…），非单轮增量。这里取最后一次的累计值；
    适配层再按线程把它去累计成本次调用的增量（与 codex token 累计处理一致）。
    codex/claude 无此字段 → 0.0。
    """
    return _extract_copilot_premium_requests(events)[0]


def _extract_copilot_premium_requests(
    events: list[dict[str, Any]] | None,
) -> tuple[float, bool]:
    if not events:
        return 0.0, False
    last = 0.0
    present = False
    for event in events:
        if not isinstance(event, dict):
            continue
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
        if usage is None:
            continue
        raw = usage.get("premiumRequests")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            last = float(raw)
            present = True
    return last, present


def _normalize_fatal_error(fatal_error: str | None) -> str | None:
    if _looks_like_recoverable_reconnect(fatal_error):
        return None
    return fatal_error


def _looks_like_recoverable_reconnect(fatal_error: str | None) -> bool:
    if not fatal_error:
        return False
    low = str(fatal_error).strip().casefold()
    match = _RECOVERABLE_RECONNECT_RE.search(low)
    return bool(match)


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _compose_interrupt_providers(*providers):
    active = [provider for provider in providers if provider is not None]
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    def _provider() -> str | None:
        for provider in active:
            reason = provider()
            if reason:
                return str(reason)
        return None

    return _provider


# --- Convenience factory ---------------------------------------------------


def build_agent_cli_backend_from_env() -> AgentCliBackend:
    """Build a AgentCliBackend from environment variables.

    Honours:

      * ``ARGUS_SKILL_RUNNER_BACKEND`` — "codex" / "claude" / "copilot"
        (default: codex)
      * ``ARGUS_SKILL_RUNNER_BIN``     — path to the CLI binary
      * ``ARGUS_SKILL_RUNNER_EXTRA_ARGS`` — space-separated default args
        appended to every command (use shell-style quoting at your own
        risk; we use ``shlex.split``).
      * ``ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS`` — stdout/stderr soft-idle
        threshold, default disabled.
      * ``ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS`` — stdout/stderr hard-idle
        threshold, default 900s.
    """
    import shlex

    backend = os.environ.get("ARGUS_SKILL_RUNNER_BACKEND") or None
    runner_bin = os.environ.get("ARGUS_SKILL_RUNNER_BIN") or None
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
        default_watchdog_hard_idle_seconds=_env_int(
            _RUNNER_HARD_IDLE_ENV,
            _RUNNER_DEFAULT_HARD_IDLE_SECONDS,
        ),
    )


__all__ = [
    "AgentCliBackend",
    "build_agent_cli_backend_from_env",
    "_strip_legacy_codex_profile_args",
]
