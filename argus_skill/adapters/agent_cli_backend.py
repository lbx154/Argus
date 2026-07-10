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
from pathlib import Path
from typing import Any

from ..core.codex_usage import sum_token_counts as _sum_token_counts
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

    def set_budget_reason_provider(self, provider) -> None:
        """Install (or clear with ``None``) the per-mission budget guard.

        ``provider() -> str | None`` is polled live: a non-empty string means the
        mission has hit its cap. It is composed into the interrupt chain so a new
        LLM call through this backend is refused at the source once the cap trips.
        The mission entry (``_SkillLoopRunner.execute``) sets this for the mission
        and clears it in a ``finally`` so it never leaks to a later mission."""
        self._budget_reason_provider = provider

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
        call_id = f"{int(time.time() * 1000)}-{threading.get_ident()}"
        log_path = self._agent_io_log_path(options)
        self._io_context.current = {
            "call_id": call_id,
            "run_label": run_label,
            "log_path": str(log_path) if log_path is not None else "",
            "model": options.model,
            "compact_io": _compact_agent_io(run_label),
        }
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
        try:
            argus_result = self._argus_runner.run_exec(
                prompt=prompt,
                resume_thread_id=resume_thread_id,
                options=argus_options,
                run_label=run_label,
            )
        except FileNotFoundError as exc:
            log.exception("codex CLI binary not found")
            self._log_agent_io(log_path, {
                "type": "agent.io.error",
                "io_kind": "error",
                "call_id": call_id,
                "run_label": run_label,
                "backend": getattr(self._argus_runner, "backend", ""),
                "error": f"runner binary not found: {exc}",
                "ts": time.time(),
            })
            self._io_context.current = None
            return RunnerResult(
                exit_code=127,
                fatal_error=f"runner binary not found: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 — last-line safety net
            log.exception("codex runner raised")
            self._log_agent_io(log_path, {
                "type": "agent.io.error",
                "io_kind": "error",
                "call_id": call_id,
                "run_label": run_label,
                "backend": getattr(self._argus_runner, "backend", ""),
                "error": f"{type(exc).__name__}: {exc}",
                "ts": time.time(),
            })
            self._io_context.current = None
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

        complete_row: dict[str, Any] = {
            "type": "agent.io.complete",
            "io_kind": "complete",
            "call_id": call_id,
            "run_label": run_label,
            "backend": getattr(self._argus_runner, "backend", ""),
            "model": options.model,
            "exit_code": getattr(argus_result, "exit_code", None),
            "thread_id": getattr(argus_result, "thread_id", None),
            "turn_completed": getattr(argus_result, "turn_completed", None),
            "turn_failed": getattr(argus_result, "turn_failed", None),
            "fatal_error": getattr(argus_result, "fatal_error", None),
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
        self._io_context.current = None
        return self._translate_result(argus_result, resume_thread_id=resume_thread_id)

    def _agent_io_log_path(self, options: RunnerOptions) -> Path | None:
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
    ) -> RunnerResult:
        (
            raw_input_tokens,
            raw_cached_input_tokens,
            raw_output_tokens,
            raw_reasoning_output_tokens,
        ) = _sum_token_counts(
            getattr(argus_result, "json_events", None)
        )
        (
            input_tokens,
            cached_input_tokens,
            output_tokens,
            reasoning_output_tokens,
        ) = self._usage_delta_for_thread(
            thread_id=argus_result.thread_id or resume_thread_id,
            raw_totals=(
                raw_input_tokens,
                raw_cached_input_tokens,
                raw_output_tokens,
                raw_reasoning_output_tokens,
            ),
        )
        premium_requests = self._premium_delta_for_thread(
            thread_id=argus_result.thread_id or resume_thread_id,
            raw_total=_sum_copilot_premium_requests(
                getattr(argus_result, "json_events", None)
            ),
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
            reasoning_output_tokens=reasoning_output_tokens,
            premium_requests=premium_requests,
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
    if not events:
        return 0.0
    last = 0.0
    for event in events:
        if not isinstance(event, dict):
            continue
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
        if usage is None:
            continue
        raw = usage.get("premiumRequests")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            last = float(raw)
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
