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
from dataclasses import replace
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
from ..core.event_catalog import EventType, normalize_event_envelope
from ..core.metrics import metrics_root_for_project, record_metric
from ..core.mission_budget import mission_cap_from_guard
from ..core.models import RunnerOptions, RunnerResult
from ..core.runner_errors import result_has_pre_provider_refusal
from ..core.secret_guard import (
    known_secret_values,
    redact_secrets_record,
    redact_secrets_text,
)
from ..core.stop_kinds import StopKind, normalize_stop_kind

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
_PROVIDER_COOLDOWN_PATTERNS = (
    "rate limit",
    "rate-limit",
    "too many requests",
    "retry after",
    "retry-after",
    "429",
    "circuit open",
    "cooldown",
)
_PROVIDER_FENCE_PATTERNS = (
    "error_max_budget_usd",
    "max budget usd",
    "max-budget-usd",
    "provider budget limit",
)
_TRANSIENT_ERROR_PATTERNS = (
    "timed out",
    "timeout",
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "stream disconnected",
    "service unavailable",
    "502",
    "503",
    "504",
)


def _compact_agent_io(run_label: str) -> bool:
    return (run_label or "").strip().lower() in _COMPACT_IO_RUN_LABELS


def _reservation_denial_stop_kind(reason: str) -> StopKind:
    low = str(reason or "").casefold()
    if "budget fence breach" in low or "unresolved provider cost" in low:
        return "provider_fence"
    if "cost control unavailable" in low:
        return "backend_unavailable"
    return "budget_exhausted"


def _raw_backend_stop_kind(
    *,
    fatal_error: str | None,
    exit_code: int,
) -> StopKind | None:
    fatal = str(fatal_error or "").strip()
    if not fatal and int(exit_code or 0) == 0:
        return None
    low = fatal.casefold()
    if low.startswith("external interrupt:"):
        return None
    if any(pattern in low for pattern in _PROVIDER_FENCE_PATTERNS):
        return "provider_fence"
    if any(pattern in low for pattern in _PROVIDER_COOLDOWN_PATTERNS):
        return "provider_cooldown"
    if any(pattern in low for pattern in _AUTH_FAILURE_PATTERNS):
        return "permanent_error"
    if any(pattern in low for pattern in _TRANSIENT_ERROR_PATTERNS):
        return "transient_error"
    if low.startswith("refused before start:"):
        return "permanent_error"
    return "backend_unavailable"


def resolve_pricing_model(
    response_model: str | None,
    request_model: str | None,
    configured_default: str | None,
) -> tuple[str, str]:
    """Pick the model id to record for pricing, with a traceable fallback source.

    Returns ``(model, fallback_source)``.  ``fallback_source`` is ``""`` when the
    model came straight from the provider response (no fallback needed); it names
    where the value was recovered from otherwise: ``"request"`` (the caller's
    configured ``options.model``), ``"configured_default"`` (the backend's
    resolved default model), or ``"none"`` (nothing usable — recorded empty so
    pricing still, honestly, marks the call ``unpriced`` and the cost gate can
    block).

    The bug this fixes: a codex call that does not pin a model — e.g. every
    ``Manager`` classify call, which builds ``RunnerOptions(...)`` with no
    ``model=`` — gets no ``model`` echoed back in the codex response, so the
    usage record used to be written with an empty model.  An empty model is
    ``unpriced``, and one unresolved ``unpriced`` call trips ``cost_control``'s
    block guard, freezing every subsequent provider call on the whole root.
    Falling back to the configured/canonical model prices the call truthfully
    (it IS the model codex used) instead of silently wedging the gate.
    """
    resp = str(response_model or "").strip()
    if resp:
        return resp, ""
    req = str(request_model or "").strip()
    if req:
        return req, "request"
    default = str(configured_default or "").strip()
    if default:
        return default, "configured_default"
    return "", "none"


def _normalize_codex_selection_args(
    args: list[str] | None,
) -> tuple[list[str], str, str, str, bool]:
    """Remove model selectors while preserving unrelated Codex CLI args."""
    cleaned: list[str] = []
    direct_model = ""
    config_model = ""
    profile = ""
    ignore_user_config = False
    values = list(args or [])
    index = 0
    while index < len(values):
        value = str(values[index] or "").strip()
        if value in {"-m", "--model"} and index + 1 < len(values):
            direct_model = str(values[index + 1] or "").strip()
            index += 2
            continue
        if value.startswith("--model="):
            direct_model = value.partition("=")[2].strip()
            index += 1
            continue
        if value in {"-c", "--config"} and index + 1 < len(values):
            payload = str(values[index + 1] or "")
            key, sep, raw = payload.partition("=")
            if sep and key.strip() == "model":
                config_model = raw.strip().strip("\"'")
            else:
                cleaned.extend([value, payload])
            index += 2
            continue
        if value.startswith("--config="):
            payload = value.partition("=")[2]
            key, sep, raw = payload.partition("=")
            if sep and key.strip() == "model":
                config_model = raw.strip().strip("\"'")
            else:
                cleaned.append(value)
            index += 1
            continue
        if value in {"-p", "--profile"} and index + 1 < len(values):
            profile = str(values[index + 1] or "").strip()
            index += 2
            continue
        if value.startswith("--profile="):
            profile = value.partition("=")[2].strip()
            index += 1
            continue
        if value == "--ignore-user-config":
            ignore_user_config = True
        cleaned.append(values[index])
        index += 1
    return cleaned, direct_model, config_model, profile, ignore_user_config


def resolve_codex_execution_model(
    request_model: str | None,
    configured_model: str | None,
    default_extra_args: list[str] | None = None,
    call_extra_args: list[str] | None = None,
) -> str:
    """Resolve one model using Codex CLI's direct/config/file precedence."""
    _cleaned, default_direct, default_config, _profile, _ignore = (
        _normalize_codex_selection_args(default_extra_args)
    )
    _cleaned, call_direct, call_config, _profile, _ignore = (
        _normalize_codex_selection_args(call_extra_args)
    )
    direct = (
        str(request_model or "").strip()
        or call_direct
        or default_direct
    )
    return (
        direct
        or call_config
        or default_config
        or str(configured_model or "").strip()
    )


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
        raw_default_extra_args = list(default_extra_args or [])
        codex_backend = chosen == deps["BACKEND_CODEX"]
        normalized_default_extra_args = (
            _normalize_codex_selection_args(raw_default_extra_args)[0]
            if codex_backend
            else raw_default_extra_args
        )
        self._argus_runner = deps["AgentCliRunner"](
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
        # reports auth-related stderr. Checked by the runtime to
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
        self._known_secret_values = known_secret_values()

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

    def _configured_pricing_model(self, *, profile: str = "") -> str:
        """Read the implicit model from Codex's own config, never another route."""
        if not self._is_codex:
            return ""
        try:
            from ..tools.capability_vault import read_codex_default_model

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
                [*(["--profile", effective_profile] if effective_profile else []),
                 *normalized_call_args]
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
        self._known_secret_values = known_secret_values()
        # Pin Codex's implicit config model before any accounting or execution.
        # The generated command, reservation, and settled usage record therefore
        # share one model id instead of independently guessing after the call.
        options = self._resolve_execution_options(options)
        # Reset per-call: the flag is checked AFTER this call completes,
        # so stale True from a previous call cannot stick across missions.
        self._auth_failure_detected = False
        call_id = uuid.uuid4().hex
        started_at = time.time()
        log_path = self._agent_io_log_path(options)
        usage_project_root, usage_mission_id = self._usage_context_snapshot()
        if usage_project_root is None and log_path is not None:
            usage_project_root = log_path.parent
        cost_reservation = None
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
            persisted_error = redact_secrets_text(
                error or str(result.fatal_error or ""),
                known_values=self._known_secret_values,
            )
            result.fatal_error = redact_secrets_text(
                str(result.fatal_error or ""),
                known_values=self._known_secret_values,
            ) or None
            result.agent_messages = [
                redact_secrets_text(
                    message,
                    known_values=self._known_secret_values,
                )
                for message in result.agent_messages
            ]
            result.stdout_lines = [
                redact_secrets_text(
                    line,
                    known_values=self._known_secret_values,
                )
                for line in result.stdout_lines
            ]
            result.stderr_lines = [
                redact_secrets_text(
                    line,
                    known_values=self._known_secret_values,
                )
                for line in result.stderr_lines
            ]
            completed_at = time.time()
            usage_record = None
            reservation_overrun_usd: float | None = None
            result.call_id = call_id
            result.stop_kind = normalize_stop_kind(result.stop_kind)
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
            if usage_project_root is not None:
                try:
                    from ..core.usage import (
                        UsageLedger,
                        build_usage_record,
                        usage_recorded_event,
                    )

                    pricing_model, model_fallback_source = resolve_pricing_model(
                        result.usage_model,
                        options.model,
                        None,
                    )
                    if model_fallback_source == "configured_default":
                        # Traceability without spamming the durable event tape:
                        # the provider response AND the request both lacked a
                        # model, so the call was priced via the configured
                        # default rather than a model the provider named.
                        log.debug(
                            "codex model id empty for %s (call %s); pricing via "
                            "configured default %s "
                            "(raw_model_empty=True, model_fallback_source=%s)",
                            run_label, call_id, pricing_model, model_fallback_source,
                        )
                    record = build_usage_record(
                        call_id=call_id,
                        project_root=usage_project_root,
                        mission_id=usage_mission_id,
                        provider=self._backend_name,
                        model=pricing_model,
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
                        error=persisted_error,
                    )
                    appended = UsageLedger(
                        usage_project_root,
                        migrate_legacy=False,
                    ).append(record)
                    usage_record = record
                    result.pricing_status = record.pricing_status
                    result.cost_usd = record.cost_usd
                    if appended:
                        self._log_agent_io(log_path, usage_recorded_event(record))
                except Exception:  # noqa: BLE001 — accounting must not break work
                    log.exception("failed to persist usage record for %s", call_id)
            if cost_reservation is not None:
                try:
                    if status == "denied":
                        cost_reservation.release(
                            reason=persisted_error or "not_started"
                        )
                        self._log_agent_io(log_path, {
                            "type": EventType.BUDGET_RESERVATION_RELEASED,
                            "reservation_id": cost_reservation.reservation_id,
                            "call_id": call_id,
                            "amount_usd": cost_reservation.amount_usd,
                            "reason": persisted_error or "not_started",
                        })
                    elif usage_record is not None:
                        reservation_overrun_usd = (
                            max(
                                0.0,
                                usage_record.cost_usd - cost_reservation.amount_usd,
                            )
                            if usage_record.cost_usd is not None
                            else None
                        )
                        cost_reservation.settle(usage_record)
                        self._log_agent_io(log_path, {
                            "type": EventType.BUDGET_RESERVATION_SETTLED,
                            "reservation_id": cost_reservation.reservation_id,
                            "call_id": call_id,
                            "amount_usd": cost_reservation.amount_usd,
                            "cost_usd": usage_record.cost_usd,
                            "overrun_usd": reservation_overrun_usd,
                            "fence_breached": bool(
                                reservation_overrun_usd
                                and reservation_overrun_usd > 0
                            ),
                            **cost_reservation.provider_fence.event_fields(),
                            "pricing_status": usage_record.pricing_status,
                        })
                    else:
                        reason = persisted_error or "usage record was not persisted"
                        cost_reservation.settle_unknown(reason=reason)
                        self._log_agent_io(log_path, {
                            "type": EventType.BUDGET_RESERVATION_SETTLED,
                            "reservation_id": cost_reservation.reservation_id,
                            "call_id": call_id,
                            "amount_usd": cost_reservation.amount_usd,
                            "cost_usd": None,
                            "overrun_usd": None,
                            "fence_breached": False,
                            **cost_reservation.provider_fence.event_fields(),
                            "pricing_status": "unknown",
                            "error": reason,
                        })
                except Exception:  # noqa: BLE001 — metering must not break work
                    log.exception("failed to settle cost reservation for %s", call_id)
            if usage_project_root is not None:
                try:
                    record_metric(
                        metrics_root_for_project(usage_project_root),
                        "provider.call",
                        labels={
                            "provider": self._backend_name,
                            "status": status,
                            "pricing_status": result.pricing_status or "unknown",
                        },
                        fields={
                            "call_id": call_id,
                            "mission_id": usage_mission_id,
                            "run_label": run_label,
                            "duration_ms": result.duration_ms,
                            "cost_usd": result.cost_usd,
                            "input_tokens": result.input_tokens,
                            "output_tokens": result.output_tokens,
                            "reservation_usd": (
                                cost_reservation.amount_usd
                                if cost_reservation is not None
                                else None
                            ),
                            "fence_enforcement": (
                                cost_reservation.provider_fence.enforcement
                                if cost_reservation is not None
                                else "none"
                            ),
                            "overrun_usd": reservation_overrun_usd,
                        },
                    )
                except Exception:  # noqa: BLE001
                    log.exception("failed to record provider metric for %s", call_id)
            self._io_context.current = None
            return result

        budget_reason = ""
        if self._budget_reason_provider is not None:
            try:
                budget_reason = str(self._budget_reason_provider() or "").strip()
            except Exception:  # noqa: BLE001 - budget probes remain fail-open
                budget_reason = ""
        if budget_reason:
            return _finalize_result(
                RunnerResult(
                    exit_code=-1,
                    thread_id=resume_thread_id,
                    fatal_error=f"refused before start: {budget_reason}",
                    stop_kind="budget_exhausted",
                ),
                status="denied",
                error=budget_reason,
            )

        # Reservation happens before the provider responds, so there is no
        # response model yet — attribute it to the request model, falling back to
        # the configured default (same rule as the settled usage record) so the
        # reservation ledger and its events never carry an empty codex model.
        reservation_model = resolve_pricing_model(
            None, options.model, None,
        )[0]
        try:
            from ..core.cost_control import (
                cost_control_enabled,
                per_call_budget_cap_usd,
                reserve_call_budget,
            )

            if cost_control_enabled():
                cost_reservation, reserve_reason = reserve_call_budget(
                    call_id=call_id,
                    project_root=usage_project_root,
                    mission_id=usage_mission_id,
                    provider=self._backend_name,
                    model=reservation_model,
                    run_label=run_label,
                    per_mission_cap_usd=mission_cap_from_guard(
                        self._budget_reason_provider
                    ),
                    per_call_cap_usd=per_call_budget_cap_usd(),
                )
                if cost_reservation is None:
                    self._log_agent_io(log_path, {
                        "type": EventType.BUDGET_RESERVATION_DENIED,
                        "call_id": call_id,
                        "provider": self._backend_name,
                        "model": reservation_model,
                        "run_label": run_label,
                        "reason": reserve_reason,
                    })
                    return _finalize_result(
                        RunnerResult(
                            exit_code=-1,
                            thread_id=resume_thread_id,
                            fatal_error=f"refused before start: {reserve_reason}",
                            stop_kind=_reservation_denial_stop_kind(reserve_reason),
                        ),
                        status="denied",
                        error=reserve_reason,
                    )
                self._log_agent_io(log_path, {
                    "type": EventType.BUDGET_RESERVATION_CREATED,
                    "reservation_id": cost_reservation.reservation_id,
                    "call_id": call_id,
                    "provider": self._backend_name,
                    "model": reservation_model,
                    "run_label": run_label,
                    "amount_usd": cost_reservation.amount_usd,
                    **cost_reservation.provider_fence.event_fields(),
                })
        except Exception as exc:  # noqa: BLE001 — fail closed before provider spend
            reason = f"cost control unavailable: {type(exc).__name__}: {exc}"
            self._log_agent_io(log_path, {
                "type": EventType.BUDGET_RESERVATION_DENIED,
                "call_id": call_id,
                "provider": self._backend_name,
                "model": reservation_model,
                "run_label": run_label,
                "reason": reason,
            })
            return _finalize_result(
                RunnerResult(
                    exit_code=-1,
                    thread_id=resume_thread_id,
                    fatal_error=f"refused before start: {reason}",
                    stop_kind="backend_unavailable",
                ),
                status="denied",
                error=reason,
            )

        if cost_reservation is not None:
            fence = cost_reservation.provider_fence
            options = replace(
                options,
                max_budget_usd=(
                    fence.limit_usd if fence.enforcement == "hard" else None
                ),
                max_ai_credits=(
                    fence.max_ai_credits if fence.enforcement == "soft" else None
                ),
            )
        try:
            argus_options = self._translate_options(options)
        except Exception as exc:  # noqa: BLE001 - release reservation on setup failure
            reason = f"runner option translation failed: {type(exc).__name__}: {exc}"
            return _finalize_result(
                RunnerResult(
                    exit_code=-1,
                    thread_id=resume_thread_id,
                    fatal_error=f"refused before start: {reason}",
                    stop_kind="permanent_error",
                ),
                status="denied",
                error=reason,
            )

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
        if interrupted:
            reason = f"External interrupt: {interrupted}"
            return _finalize_result(
                RunnerResult(
                    exit_code=-1,
                    thread_id=resume_thread_id,
                    fatal_error=reason,
                ),
                status="denied",
                error=reason,
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
                    "type": EventType.PROVIDER_REQUEST_DENIED,
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
                        stop_kind=normalize_stop_kind(copilot_permit.stop_kind),
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
                    "type": EventType.PROVIDER_REQUEST_DENIED,
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
                        stop_kind=normalize_stop_kind(codex_permit.stop_kind),
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
                "type": EventType.PROVIDER_REQUEST_STARTED,
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
            safe_error_text = redact_secrets_text(
                error_text,
                known_values=self._known_secret_values,
            )
            if copilot_permit is not None:
                copilot_permit.finish(
                    premium_requests=premium_requests,
                    error_text=safe_error_text,
                    success=success,
                )
            if codex_permit is not None:
                codex_permit.finish(success=success, error_text=safe_error_text)
            if event_permit is not None:
                self._log_agent_io(log_path, {
                    "type": EventType.PROVIDER_REQUEST_COMPLETED,
                    "provider": self._backend_name,
                    "call_id": call_id,
                    "run_label": run_label,
                    "success": bool(success),
                    "error": (safe_error_text or "")[:500],
                    "daily_calls": int(getattr(event_permit, "daily_calls", 0) or 0),
                    "daily_cap": int(getattr(event_permit, "daily_cap", 0) or 0),
                    "premium_requests": float(premium_requests or 0.0),
                    "ts": time.time(),
                })

        start_row: dict[str, Any] = {
            "type": EventType.AGENT_IO_START,
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
                "type": EventType.AGENT_IO_ERROR,
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
                    stop_kind="permanent_error",
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
                "type": EventType.AGENT_IO_ERROR,
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
                    stop_kind="backend_unavailable",
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
                    stop_kind="backend_unavailable",
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
        safe_failure_text = redact_secrets_text(
            failure_text,
            known_values=self._known_secret_values,
        )
        pre_provider_refusal = bool(
            result_has_pre_provider_refusal(argus_result)
            and translated.total_nano_aiu is None
            and not translated.model_usage
            and not translated.premium_requests_present
            and not any((
                translated.input_tokens_present,
                translated.cached_input_tokens_present,
                translated.cache_write_tokens_present,
                translated.output_tokens_present,
                translated.reasoning_output_tokens_present,
            ))
        )

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
            error_text=safe_failure_text,
            success=not failed,
        )

        complete_row: dict[str, Any] = {
            "type": EventType.AGENT_IO_COMPLETE,
            "io_kind": "complete",
            "call_id": call_id,
            "run_label": run_label,
            "backend": getattr(self._argus_runner, "backend", ""),
            "model": translated.usage_model or options.model,
            "exit_code": getattr(argus_result, "exit_code", None),
            "thread_id": getattr(argus_result, "thread_id", None),
            "turn_completed": getattr(argus_result, "turn_completed", None),
            "turn_failed": getattr(argus_result, "turn_failed", None),
            "fatal_error": redact_secrets_text(
                str(getattr(argus_result, "fatal_error", "") or ""),
                known_values=self._known_secret_values,
            ) or None,
            "tool_activity_observed": bool(
                getattr(argus_result, "tool_activity_observed", False)
            ),
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
            status=(
                "denied"
                if pre_provider_refusal
                else "error"
                if failed
                else "completed"
            ),
            error=safe_failure_text,
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
        safe_row = redact_secrets_record(
            normalize_event_envelope(row),
            known_values=self._known_secret_values,
        )
        _jsonl_append(path, safe_row, self._io_log_lock)

    def _stream_event_callback(self, stream: str, line: str) -> None:
        ctx = getattr(self._io_context, "current", None) or {}
        log_path = str(ctx.get("log_path") or "")
        canonical_stream = stream.rsplit(".", 1)[-1]
        if canonical_stream not in {"stdout", "stderr"}:
            canonical_stream = "stdout"
        safe_line = redact_secrets_text(
            line,
            known_values=self._known_secret_values,
        )
        if log_path and not bool(ctx.get("compact_io")):
            self._log_agent_io(Path(log_path), {
                "type": EventType.AGENT_IO_STREAM,
                "io_kind": "stream",
                "call_id": ctx.get("call_id"),
                "run_label": ctx.get("run_label"),
                "backend": getattr(self._argus_runner, "backend", ""),
                "model": ctx.get("model"),
                "stream": canonical_stream,
                "line": safe_line,
                "ts": time.time(),
            })
        if self._external_event_callback is not None:
            self._external_event_callback(stream, safe_line)

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
        if "sandbox_mode" in getattr(argus_cls, "__dataclass_fields__", {}):
            kwargs["sandbox_mode"] = getattr(options, "sandbox_mode", None)
        # Forward the live assistant-block callback the same guarded way — only
        # the Manager chat front-door sets it, and a vendored copy without the
        # field degrades to no streaming rather than crashing.
        if "on_agent_message" in getattr(argus_cls, "__dataclass_fields__", {}):
            kwargs["on_agent_message"] = getattr(options, "on_agent_message", None)
        if "max_budget_usd" in getattr(argus_cls, "__dataclass_fields__", {}):
            kwargs["max_budget_usd"] = getattr(options, "max_budget_usd", None)
        if "max_ai_credits" in getattr(argus_cls, "__dataclass_fields__", {}):
            kwargs["max_ai_credits"] = getattr(options, "max_ai_credits", None)
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
            stop_kind=(
                normalize_stop_kind(getattr(argus_result, "stop_kind", None))
                or _raw_backend_stop_kind(
                    fatal_error=argus_result.fatal_error,
                    exit_code=argus_result.exit_code,
                )
            ),
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
            tool_activity_observed=bool(
                getattr(argus_result, "tool_activity_observed", False)
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
