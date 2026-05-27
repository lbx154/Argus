"""Real LLM backend: thin adapter over ArgusBot's ``CodexRunner``.

argus-skill's loop is deliberately backend-agnostic — it talks to a
``RunnerBackend`` (Protocol) defined in ``argus_skill.core.ports``. The
deterministic ``MemoryBackend`` is fine for tests, but for *real* runs
we need to drive the actual codex / claude / copilot CLI.

ArgusBot already ships a battle-tested subprocess wrapper —
``codex_autoloop.codex_runner.CodexRunner`` — that handles JSON event
streams, idle watchdogs, claude/copilot dialects, and cross-platform
stdin quirks. Re-vendoring would mean carrying ~700 LOC + tests of
edge-case bug fixes. So instead this adapter *wraps* it.

Provenance: new code. Depends on ArgusBot being importable
(``pip install 'argus-skill[codex]'``).

The translation layer:

  argus-skill's ``RunnerOptions``   →   ArgusBot's ``RunnerOptions``
  argus-skill's ``run_label`` kwarg →   ArgusBot's ``run_label``
  ArgusBot's   ``CodexRunResult``   →   argus-skill's ``RunnerResult``

Field names are mostly 1:1 (both projects evolved from the same
ancestor); we keep only the slim subset argus-skill needs.

Token usage is best-effort — codex's JSON event stream emits
``token_count.input_tokens`` / ``output_tokens`` in some events; we
sum them across the run when present. When unavailable we leave them
at 0 (the loop never branches on token counts).
"""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any

from ..core.models import RunnerOptions, RunnerResult

log = logging.getLogger(__name__)


_AUTH_FAILURE_PATTERNS: tuple[str, ...] = (
    "unauthorized",
    "expired token",
    "invalid token",
    "authentication failed",
    "401",
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


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


# --- ArgusBot import (lazy, with friendly error) ---------------------------

def _import_argusbot():
    try:
        from codex_autoloop.codex_runner import (
            CodexRunner,
        )
        from codex_autoloop.codex_runner import (
            RunnerOptions as ArgusRunnerOptions,
        )
        from codex_autoloop.runner_backend import (
            BACKEND_CLAUDE,
            BACKEND_CODEX,
            BACKEND_COPILOT,
            DEFAULT_RUNNER_BACKEND,
            default_runner_bin,
            normalize_runner_backend,
        )
    except ImportError as exc:  # pragma: no cover - environmental
        raise ImportError(
            "CodexRunnerBackend requires ArgusBot to be importable. "
            "Install with `pip install 'argus-skill[codex]'` (or add it to PYTHONPATH)."
        ) from exc
    return {
        "CodexRunner": CodexRunner,
        "ArgusRunnerOptions": ArgusRunnerOptions,
        "BACKEND_CLAUDE": BACKEND_CLAUDE,
        "BACKEND_CODEX": BACKEND_CODEX,
        "BACKEND_COPILOT": BACKEND_COPILOT,
        "DEFAULT_RUNNER_BACKEND": DEFAULT_RUNNER_BACKEND,
        "default_runner_bin": default_runner_bin,
        "normalize_runner_backend": normalize_runner_backend,
    }


# --- The adapter -----------------------------------------------------------


class CodexRunnerBackend:
    """``RunnerBackend`` implementation that shells out to a real CLI.

    Construct once with the runner backend choice ("codex" / "claude" /
    "copilot") and any cross-call defaults (e.g. ``default_extra_args``
    for ``-c "config_profile=..."``), then pass the same instance to
    every ``SkillLoop`` actor (scientist / engineer / reviewer). Each
    ``run_exec`` call spawns a fresh subprocess.

    Threading: the underlying ``CodexRunner.run_exec`` is blocking and
    not designed to be called concurrently from one instance — but
    multiple ``CodexRunnerBackend`` calls *are* safe in series. Use
    separate instances if you want concurrent matcher + scientist +
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
        self._argus_runner = deps["CodexRunner"](
            codex_bin=runner_bin,
            backend=chosen,
            event_callback=event_callback,
            default_extra_args=default_extra_args,
            before_exec=before_exec,
        )
        self._default_interrupt_reason_provider = default_interrupt_reason_provider
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
        self._thread_usage_totals: dict[str, tuple[int, int, int]] = {}

    @property
    def argus_runner(self):
        """Expose the underlying ArgusBot ``CodexRunner`` instance.

        Needed by ``MissionDaemon`` so it can hand the same runner to
        ArgusBot's ``Reviewer`` / ``Planner`` / report fallback paths
        without constructing a second codex subprocess wrapper.
        """
        return self._argus_runner

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
        try:
            argus_result = self._argus_runner.run_exec(
                prompt=prompt,
                resume_thread_id=resume_thread_id,
                options=argus_options,
                run_label=run_label,
            )
        except FileNotFoundError as exc:
            log.exception("codex CLI binary not found")
            return RunnerResult(
                exit_code=127,
                fatal_error=f"runner binary not found: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 — last-line safety net
            log.exception("codex runner raised")
            return RunnerResult(
                exit_code=-1,
                fatal_error=f"{type(exc).__name__}: {exc}",
            )

        # 7×24 survivability: codex auth tokens expire silently. Detect
        # the well-known stderr patterns and log a warning so the daemon
        # surfaces it instead of looping over failing missions all night.
        # Only flag auth failure when the run actually FAILED — Azure
        # backends sometimes emit transient 401 warnings in stderr even
        # when the run succeeds (rate-limit retries, etc.).
        if (
            argus_result.exit_code != 0
            and looks_like_auth_failure(getattr(argus_result, "stderr_lines", None))
        ):
            self._auth_failure_detected = True
            log.warning(
                "codex backend reported auth-related stderr "
                "(run_label=%s, exit_code=%d) — run `codex login` to refresh credentials",
                run_label, argus_result.exit_code,
            )

        return self._translate_result(argus_result, resume_thread_id=resume_thread_id)

    # --- helpers ----------------------------------------------------------

    def _translate_options(self, options: RunnerOptions):
        argus_cls = self._deps["ArgusRunnerOptions"]
        # ArgusBot's RunnerOptions is a superset (has watchdog hooks,
        # add_dirs, plugin_dirs, etc.). Forward the fields argus-skill
        # exposes; the watchdog hooks are propagated when set so an
        # outer supervisor can interrupt the codex subprocess.
        interrupt_provider = _compose_interrupt_providers(
            self._default_interrupt_reason_provider,
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
        return argus_cls(
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

    def _translate_result(
        self,
        argus_result,
        *,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        raw_input_tokens, raw_cached_input_tokens, raw_output_tokens = _sum_token_counts(
            getattr(argus_result, "json_events", None)
        )
        input_tokens, cached_input_tokens, output_tokens = self._usage_delta_for_thread(
            thread_id=argus_result.thread_id or resume_thread_id,
            raw_totals=(raw_input_tokens, raw_cached_input_tokens, raw_output_tokens),
        )
        return RunnerResult(
            exit_code=argus_result.exit_code,
            agent_messages=list(argus_result.agent_messages or []),
            stdout_lines=list(argus_result.stdout_lines or []),
            stderr_lines=list(argus_result.stderr_lines or []),
            thread_id=argus_result.thread_id,
            fatal_error=_normalize_fatal_error(argus_result.fatal_error),
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
        )

    def _usage_delta_for_thread(
        self,
        *,
        thread_id: str | None,
        raw_totals: tuple[int, int, int],
    ) -> tuple[int, int, int]:
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


def _sum_token_counts(events: list[dict[str, Any]] | None) -> tuple[int, int, int]:
    """Best-effort token accounting from the codex JSON event stream.

    The codex CLI emits events with shapes like::

        {"type": "token_count", "input_tokens": 1234, "output_tokens": 567}

    or, in older versions, a ``msg`` envelope::

        {"type": "msg", "content": {..., "input_tokens": ...}}

    We pick the complete tuple from the final token-bearing event rather
    than summing — codex emits running totals, not per-event deltas. Zero
    is a valid value in that final tuple (for example, no cached input).
    If the run produced no countable events we return (0, 0, 0).
    """
    if not events:
        return 0, 0, 0
    last: tuple[int, int, int] = (0, 0, 0)
    for event in events:
        if not isinstance(event, dict):
            continue
        # Newer codex events (>=0.121): usage nested under top-level "usage".
        #   {"type":"turn.completed","usage":{"input_tokens":..,"output_tokens":..}}
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
        in_tok = 0
        cached_tok = 0
        out_tok = 0
        if usage is not None:
            in_tok = _coerce_int(usage.get("input_tokens"))
            cached_tok = _coerce_int(usage.get("cached_input_tokens"))
            out_tok = _coerce_int(usage.get("output_tokens"))
        # Fallback: top-level fields (older codex / token_count event).
        if in_tok == 0:
            in_tok = _coerce_int(event.get("input_tokens"))
        if cached_tok == 0:
            cached_tok = _coerce_int(event.get("cached_input_tokens"))
        if out_tok == 0:
            out_tok = _coerce_int(event.get("output_tokens"))
        # Older codex events: nested under 'msg' / 'content'.
        if in_tok == 0 or out_tok == 0:
            content = event.get("content") if isinstance(event.get("content"), dict) else None
            if content is not None:
                if in_tok == 0:
                    in_tok = _coerce_int(content.get("input_tokens"))
                if cached_tok == 0:
                    cached_tok = _coerce_int(content.get("cached_input_tokens"))
                if out_tok == 0:
                    out_tok = _coerce_int(content.get("output_tokens"))
        if in_tok > 0 or cached_tok > 0 or out_tok > 0:
            last = (in_tok, cached_tok, out_tok)
    return last


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


def build_codex_backend_from_env() -> CodexRunnerBackend:
    """Build a CodexRunnerBackend from environment variables.

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
    extra = shlex.split(raw_extra) if raw_extra else None
    return CodexRunnerBackend(
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
    "CodexRunnerBackend",
    "build_codex_backend_from_env",
]
