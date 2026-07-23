"""Runner construction: ``_RunnerConstructionMixin`` (the
``_SkillLoopRunner.__init__``/backend-wiring half of the runner) plus the
module-level factory helpers (``build_life_runner`` and its backend-name
resolution helpers).

Split out of ``_runtime.py`` so that module stays under the maintainability
line-count target. Every name here is re-exported from ``_runtime.py`` (see
its module docstring and ``__all__``) so external imports are unaffected.
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core.ports import EventSink
from ..core.run_gateway import run_exec as gateway_run_exec
from ._env import env_int as _env_int
from ._runtime_backends import _MemoryRunner, _ScriptedPlannerBackend
from ._runtime_helpers import _SplitMemory

log = logging.getLogger(__name__)


class _RunnerConstructionMixin:
    """Backend/manager construction half of ``_SkillLoopRunner``.

    Combined with :class:`~._runtime_execute.SkillLoopExecuteMixin`,
    :class:`~._runtime_stage_transition.StageTransitionMixin`, and
    :class:`~._self_reply.SelfReplyMixin` by the ``_SkillLoopRunner`` facade
    class in ``_runtime.py``.
    """

    def __init__(self, args: argparse.Namespace, *, seed_thread_id: str | None = None) -> None:
        from ..loop import SkillLoop, SkillLoopConfig

        self._SkillLoop = SkillLoop
        self._SkillLoopConfig = SkillLoopConfig
        try:
            from ..adapters.agent_cli_backend import AgentCliBackend
        except ImportError as exc:  # pragma: no cover — depends on optional install
            raise SystemExit(
                f"Codex backend requested but the bundled agent_cli runtime "
                f"is unavailable: {exc}.\n"
                "Reinstall argus-skill to restore it."
            ) from exc
        # Per-call sink swap: backend is built once, but the sink rotates
        # for every execute(). A trampoline callback dispatches to the
        # currently-installed sink so codex's stream-json events become
        # ``engineer.progress`` items in whichever sink owns this call.
        self._current_sink: EventSink | None = None
        # Per-mission ledger of failed tool/command beats. Reset on every
        # execute() so warnings don't bleed across missions.
        self._current_failure_ledger: object | None = None
        # ONE stream-progress callback, reused across stdout lines. It closes over
        # copilot's delta-accumulation buffer, which must persist line-to-line —
        # rebuilding it per line (the old bug here) reset the buffer every token,
        # so copilot's per-token reply deltas were emitted as standalone fragments
        # and the cockpit showed one word per line. The relay rebuilds only when
        # the sink/ledger changes (a new mission). See ``StreamProgressRelay``.
        from ..adapters.stream_progress import StreamProgressRelay

        self._stream_progress_relay = StreamProgressRelay()

        def _trampoline(stream: str, line: str) -> None:
            sink = self._current_sink
            if sink is None:
                return
            try:
                self._stream_progress_relay(sink, self._current_failure_ledger, stream, line)
            except Exception:  # noqa: BLE001 — never let logging crash the runner
                pass

        # Mirror build_agent_cli_backend_from_env's env-var contract here so
        # we can also pass event_callback (the helper doesn't expose it).
        from ..adapters.agent_cli_backend import _strip_legacy_codex_profile_args

        # An explicit env override wins, else honour the backend the caller
        # already resolved into ``args.backend`` (which includes the persisted
        # ``/backend`` knob). Env-only reads here silently fell back to codex for
        # the in-process Manager front-door — see ``_resolve_runner_backend_name``.
        backend_name = _resolve_runner_backend_name(args)
        runner_bin = os.environ.get("ARGUS_SKILL_RUNNER_BIN") or None
        from ..agent_cli.runner_backend import (
            normalize_runner_backend,
            resolve_available_runner,
        )

        requested_backend = normalize_runner_backend(backend_name)
        backend_name, runner_bin = resolve_available_runner(
            backend_name,
            runner_bin,
        )
        if backend_name != requested_backend:
            log.warning(
                "%s runner is unavailable; using %s at %s",
                requested_backend,
                backend_name,
                runner_bin,
            )
        raw_extra = os.environ.get("ARGUS_SKILL_RUNNER_EXTRA_ARGS", "").strip()
        extra = _strip_legacy_codex_profile_args(shlex.split(raw_extra) if raw_extra else None)
        stop_event = getattr(args, "stop_event", None)
        # Set ONLY by the real 7×24 daemon's own namespace builder (see
        # ``daemon/life_worker.py:_runner_namespace``) — never by the
        # front-door quick-reply runner
        # or by the test/legacy ``_invoke_supervisor`` path. This is what
        # lets the Manager (running in the operator-facing API process)
        # ask the daemon to abort whatever mission it is currently executing:
        # the request is a small mailbox file in the shared life_dir, and only
        # the runner that is actually
        # driving a real mission round should ever consume it. Gating
        # explicitly (rather than piggybacking on ``stop_event is not None``)
        # keeps this correct even if a future change wires a Ctrl-C
        # ``stop_event`` into one of those other runners for an unrelated
        # reason — it must never let the Manager's own SELF-turn (which
        # raises the abort request as one of ITS OWN tool calls) accidentally
        # kill itself mid-reply.
        self._enable_mission_abort_signal = bool(
            getattr(args, "enable_mission_abort_signal", False)
        )

        def _stop_reason() -> str | None:
            if stop_event is not None and stop_event.is_set():
                return "daemon stop requested"
            if self._enable_mission_abort_signal:
                from ..life.memory import consume_running_item_abort

                abort_reason = consume_running_item_abort(
                    getattr(self, "_manager_session_root", None)
                )
                if abort_reason:
                    return f"operator abort requested: {abort_reason}"
            return None

        self._backend = AgentCliBackend(
            backend=backend_name,
            runner_bin=runner_bin,
            default_extra_args=extra,
            default_interrupt_reason_provider=_stop_reason if stop_event is not None else None,
            default_watchdog_soft_idle_seconds=_env_int(
                "ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS",
                10 * 60,
            ),
            default_watchdog_stalled_idle_seconds=_env_int(
                "ARGUS_SKILL_RUNNER_STALLED_IDLE_SECONDS",
                30 * 60,
            ),
            default_watchdog_hard_idle_seconds=_env_int(
                "ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS",
                45 * 60,
            ),
            event_callback=_trampoline,
        )
        # Expose the underlying backend so the LifeSupervisor's
        # iteration loop can drive a Critic agent through it without
        # building a second codex process.
        self.backend = self._backend

        # Per-role backends. Each agent role (engineer / reviewer / planner /
        # manager) can be pinned to its OWN backend via
        # ``ARGUS_SKILL_{ROLE}_BACKEND`` (codex / claude / copilot / opencode) plus an
        # optional ``ARGUS_SKILL_{ROLE}_RUNNER_BIN``. When neither is set the
        # role SHARES the single default backend above — so the common case
        # still builds exactly one CLI process and behaviour is unchanged. Set
        # an override only when you want, e.g., the reviewer on a different
        # provider than the engineer.
        def _role_backend(role: str):
            role_backend_name = _resolve_role_runner_backend_name(
                role,
                backend_name,
            )
            bin_env = os.environ.get(f"ARGUS_SKILL_{role.upper()}_RUNNER_BIN", "").strip()
            from ..agent_cli.runner_backend import (
                normalize_runner_backend,
                resolve_available_runner,
            )

            requested = normalize_runner_backend(role_backend_name)
            same_type = normalize_runner_backend(backend_name) == requested
            if same_type and not bin_env:
                return self._backend
            chosen, role_bin = resolve_available_runner(
                role_backend_name,
                bin_env or None,
            )
            if chosen != requested:
                log.warning(
                    "%s %s runner is unavailable; using %s at %s",
                    role,
                    requested,
                    chosen,
                    role_bin,
                )
            if (
                chosen == normalize_runner_backend(backend_name)
                and not bin_env
                and role_bin == runner_bin
            ):
                return self._backend
            return AgentCliBackend(
                backend=chosen,
                runner_bin=role_bin,
                default_extra_args=extra,
                default_interrupt_reason_provider=(
                    _stop_reason if stop_event is not None else None
                ),
                default_watchdog_soft_idle_seconds=_env_int(
                    "ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS",
                    10 * 60,
                ),
                default_watchdog_stalled_idle_seconds=_env_int(
                    "ARGUS_SKILL_RUNNER_STALLED_IDLE_SECONDS",
                    30 * 60,
                ),
                default_watchdog_hard_idle_seconds=_env_int(
                    "ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS",
                    45 * 60,
                ),
                event_callback=_trampoline,
            )

        self.engineer_backend = _role_backend("engineer")
        self.reviewer_backend = _role_backend("reviewer")
        self.planner_backend = _role_backend("planner")
        self.manager_backend = _role_backend("manager")
        self.curator_backend = _role_backend("curator")
        self._args = args
        raw_usage_root = str(getattr(args, "project_state_dir", "") or "").strip()
        self._usage_project_root = Path(raw_usage_root).expanduser() if raw_usage_root else None
        raw_global_root = str(getattr(args, "global_root", "") or "").strip()
        self._usage_global_root = Path(raw_global_root).expanduser() if raw_global_root else None
        if self._usage_global_root is None and self._usage_project_root is not None:
            parent = self._usage_project_root.parent
            if parent.name == "projects":
                self._usage_global_root = parent.parent
        self._active_usage_mission_id: str | None = None
        self._set_usage_context(None)
        # The ONE Manager instance for this runner. All daemon-side Manager uses
        # (divide / is_conversational / skill placement) go through this single
        # instance on the manager backend — no more scattered ad-hoc
        # ``Manager(...)`` constructions, and skill approval now genuinely runs
        # on the Manager's backend rather than the reviewer's.
        from ..manager import Manager

        _manager_workdir = (
            Path(args.workdir).expanduser() if getattr(args, "workdir", None) else Path.cwd()
        )
        _manager_session_root = (
            Path(getattr(args, "manager_session_root")).expanduser()
            if getattr(args, "manager_session_root", None)
            else _manager_workdir
        )
        # ``_artifact_root`` / Manager's ``project_root`` MUST be the real
        # mission WORKDIR, never the daemon's internal life_dir: every OTHER
        # reader/writer of ``research/PIPELINE_STATE.json`` (stage_machine.
        # current_stage/advance_stage, the reviewer's stage-gated checklist,
        # engineer/runner.py's stage-based branching, resolve_vertical, custom
        # data-domain lookups) operates against the WORKDIR. Pointing the
        # Manager's stage-authority writes at ``_manager_session_root`` (life_dir
        # in daemon/continuous mode — see life_worker.py's
        # ``ns.manager_session_root = str(cfg.life_dir)``) silently splits the
        # pipeline state in two: the Manager advances/rolls-back a
        # PIPELINE_STATE.json under life_dir that NOTHING else ever reads, while
        # every stage-gated check in the real mission workdir keeps falling back
        # to the vertical's first stage forever (observed in production: a
        # kernelbench mission whose life_dir copy legitimately reached
        # "measure", 8 kernels deep, while its workdir copy never existed —
        # the mission's own tooling correctly observed "no
        # research/PIPELINE_STATE.json here" and got stuck waiting on a
        # transition that had already happened, just in the wrong place).
        # ``manager_session_root`` is unaffected: it stays daemon/life_dir-scoped
        # for the Manager's OWN persistent codex session/lock files only (see
        # ``_ManagerSession``), which is an orthogonal concern.
        self._artifact_root = _manager_workdir
        os.environ["ARGUS_SKILL_ARTIFACT_ROOT"] = str(_manager_workdir)
        # Skill matcher for the Manager (same adaptive library the SkillLoop/
        # planner/reviewer match against). Pointed at the daemon's skills dir so
        # the Manager injects its fixed role skill plus any matched manager skill
        # into its stage-decision prompt. Fail-soft: any error → None, and the
        # Manager simply runs without an injected skill block (unchanged
        # behaviour), since this must never block daemon start-up.
        self._manager_skill_store = self._build_manager_skill_store(args)
        self.manager = Manager(
            project_root=_manager_workdir,
            runner=self.manager_backend or self._backend,
            skill_store=self._manager_skill_store,
            manager_session_root=_manager_session_root,
            usage_context=self.task_usage_context,
        )
        self._manager_session_root = _manager_session_root
        # Session continuity: seed_thread_id is the codex session id from
        # the previous mission in the same Manager session. We propagate it
        # into the *first* engineer round of this mission, then update
        # in-place after each execute() so the cockpit can recover the
        # latest thread_id and forward it to the next mission.
        self._next_seed_thread_id: str | None = seed_thread_id
        self.last_thread_id: str | None = seed_thread_id
        # Chat fast-path is operator-front-door-only: enabled per invocation by
        # ``_invoke_supervisor`` for human free-text typed at the cockpit.
        # Defaults False so planner / backlog / daemon missions are never
        # classified — the harness must not second-guess agent-produced work.
        self._allow_chat_fast_path: bool = False

    def _build_manager_skill_store(self, args: argparse.Namespace) -> Any:
        """Build the Manager's skill matcher store from the daemon's skills dir.

        Mirrors the SkillLoop's own ``SkillStore`` (same dir, same matcher model)
        so the Manager matches against the SAME adaptive library the engineer/
        reviewer/planner do. Fail-soft: any error returns ``None`` and the Manager
        runs without an injected skill block (unchanged behaviour) — building this
        store must never block daemon start-up.
        """
        try:
            from ..loop import SkillLoopConfig
            from ..skills.layered import (
                LayeredSkillStore,
                shared_skill_scope_dir,
            )
            from ..skills.store import SkillStore
            from ..skills.vertical_select import resolve_skill_scope

            # A default config is enough for the matcher: ``resolved_matcher_model``
            # already applies the ``ARGUS_SKILL_MATCHER_MODEL`` env override, and
            # ``matcher_reasoning_effort`` defaults to the same value the SkillLoop
            # uses. We only need the matcher knobs here, not the full mission cfg.
            cfg = SkillLoopConfig()
            global_dir = Path(args.skills_dir)
            project_state_dir = str(getattr(args, "project_state_dir", "") or "").strip()
            if project_state_dir:
                workdir = Path(args.workdir).expanduser() if args.workdir else Path.cwd()
                active_skill_scope = resolve_skill_scope(workdir)
                return LayeredSkillStore(
                    project_dir=Path(project_state_dir) / "skills",
                    global_dir=global_dir,
                    vertical_dir=shared_skill_scope_dir(
                        global_dir,
                        active_skill_scope,
                    ),
                    runner=self.manager_backend or self._backend,
                    matcher_model=cfg.resolved_matcher_model(),
                    matcher_reasoning_effort=cfg.matcher_reasoning_effort,
                )
            return SkillStore(
                global_dir,
                runner=self.manager_backend or self._backend,
                matcher_model=cfg.resolved_matcher_model(),
                matcher_reasoning_effort=cfg.matcher_reasoning_effort,
            )
        except Exception:  # noqa: BLE001 — never block start-up on the matcher
            log.debug("manager skill store build skipped", exc_info=True)
            return None

    def shared_skills_root(self) -> Path:
        """Return the exact shared Skill directory used by this runner."""
        return Path(self._args.skills_dir)

    def _refresh_manager_skill_store(self, args: argparse.Namespace) -> None:
        """Refresh Manager matching after the Manager persists a vertical."""
        store = self._build_manager_skill_store(args)
        if store is None:
            return
        self._manager_skill_store = store
        self.manager.skill_store = store
        from ..skills.missions import ManagerMission

        self.manager.mission = ManagerMission(store)

    def stream_to(self, sink: EventSink):
        """Context manager: temporarily route stream lines to *sink*.

        Use this when calling the execution gateway directly (critic /
        planner) outside the normal ``execute()`` path so that streaming
        events still flow through the trampoline to the event sink.
        """
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            prev = self._current_sink
            self._current_sink = sink
            try:
                yield
            finally:
                self._current_sink = prev

        return _ctx()

    def run_exec(self, **kwargs):
        """Proxy to the manager backend so manager-side skill-library gates can
        run_exec against this runner directly.

        ``Manager.classify_skill_placement`` passes
        ``runner=(self._session or self.runner)``; on the daemon ``_session`` is
        this ``_SkillLoopRunner``, which had no ``run_exec`` — so both the skill
        gate and placement raised ``AttributeError`` (caught → distillation
        silently no-op'd). Delegate to the same backend the Manager itself uses.
        """
        backend = self.manager_backend or self._backend
        return gateway_run_exec(backend, **kwargs)

    def _distinct_backends(self) -> list:
        """The distinct role AgentCliBackend instances this runner drives."""
        seen: set[int] = set()
        out: list = []
        for be in (
            getattr(self, "_backend", None),
            getattr(self, "engineer_backend", None),
            getattr(self, "reviewer_backend", None),
            getattr(self, "planner_backend", None),
            getattr(self, "curator_backend", None),
            getattr(self, "manager_backend", None),
        ):
            if be is not None and id(be) not in seen:
                seen.add(id(be))
                out.append(be)
        return out

    def _set_usage_context(self, mission_id: str | None) -> list:
        """Point every role backend at this project's call ledger."""
        self._active_usage_mission_id = mission_id
        backends = self._distinct_backends()
        for backend in backends:
            setter = getattr(backend, "set_usage_context", None)
            if setter is None:
                continue
            try:
                setter(
                    project_root=self._usage_project_root,
                    global_root=self._usage_global_root,
                    mission_id=mission_id,
                )
            except Exception:  # noqa: BLE001 — metering must not break a mission
                pass
        return backends

    @contextmanager
    def task_usage_context(self, mission_id: str | None):
        previous = getattr(self, "_active_usage_mission_id", None)
        self._set_usage_context(mission_id)
        try:
            yield
        finally:
            self._set_usage_context(previous)

    def _consume_auth_failure(self) -> bool:
        """Read and clear auth/policy failure flags across every role backend."""
        failed = False
        for backend in self._distinct_backends():
            if bool(getattr(backend, "_auth_failure_detected", False)):
                failed = True
                try:
                    backend._auth_failure_detected = False
                except Exception:  # noqa: BLE001
                    pass
        return failed


def _format_daemon_mode_cell(theme, mem: _SplitMemory) -> str:  # noqa: ANN001
    """Banner ``executor`` cell — the honest one-line daemon state.

    Shows ``life ● daemon: pid X · up Y · draining`` when a 7×24 worker is
    draining this project's backlog, or ``life · no daemon`` when not. Only the
    daemon drains the backlog; the operator front-end never executes missions.

    Uses the plain ``●`` status dot (as everywhere else — /roles, /daemons),
    NOT an emoji: a lightning/gear/etc. emoji has East-Asian *ambiguous/wide*
    width and desyncs column math next to the CJK text on this line, producing
    the "字符错位" corruption the tui glyph test guards against.
    """
    try:
        from ..daemon.life_worker import read_daemon_status
        from .cli import _format_short_duration

        status = read_daemon_status(mem.project.root)
    except Exception:  # noqa: BLE001
        return (
            f"{theme.bold('life')}    "
            + theme.yellow("no daemon")
            + theme.dim(" — tasks queue until `argus-skill --daemon`")
        )
    if status.alive and status.pid is not None:
        uptime = _format_short_duration(status.uptime_seconds or 0.0)
        body = (
            f"{theme.bold('life')}  "
            + theme.bold_green("● daemon")
            + theme.dim(f": pid {status.pid} · up {uptime} · draining")
        )
        return body
    return (
        f"{theme.bold('life')}    "
        + theme.yellow("no daemon")
        + theme.dim("  — tasks queue until you start one (`argus-skill --daemon`)")
    )


def _codex_preflight_warning() -> str | None:
    """Return a one-line warning if the configured runner backend's CLI
    cannot run, else None.

    Surfaced on the banner so the user does not discover at mission time
    that the bundled agent_cli runtime or the configured CLI binary are
    missing. Best-effort: if anything raises we stay quiet — a confusing
    warning is worse than no warning, and the real failure path
    (``_SkillLoopRunner``) will print a precise error when a mission
    actually starts.
    """
    try:
        from ..adapters.agent_cli_backend._runtime import load_agent_cli_runtime
    except ImportError:
        return "bundled agent_cli module not importable — reinstall argus-skill"
    try:  # noqa: SIM105
        load_agent_cli_runtime()
    except Exception:  # noqa: BLE001
        return "bundled agent_cli failed to load — check the argus-skill install"
    # BUG FIX: this used to hardcode `shutil.which("codex")` regardless of
    # which CLI is actually configured, so an operator running entirely on
    # ARGUS_SKILL_RUNNER_BACKEND=claude/copilot/opencode (no `codex` npm package
    # installed at all, by design) got a false "codex binary not found"
    # warning on every banner / `/doctor` run. Check whichever backend is
    # actually configured; "codex" (the default) keeps its exact original
    # message for backward compatibility.
    from ..agent_cli.runner_backend import resolve_runner_bin
    from ..core.knobs import resolve_role_backend

    backend = resolve_role_backend("")
    bin_path = resolve_runner_bin(
        backend,
        os.environ.get("ARGUS_SKILL_RUNNER_BIN") or None,
    )
    if not bin_path:
        if backend == "codex":
            hint = "install with `npm install -g @openai/codex`"
        else:
            hint = f"install the `{backend}` CLI"
        return f"`{backend}` binary not found on PATH — {hint} or set ARGUS_SKILL_RUNNER_BIN"
    return None


def _inbox_drainer_for(
    life_dir: Path,
    *,
    project_root: Path | None = None,
):
    """Return a `user_inbox` callable that drains pending messages from
    ``<life_dir>/inbox.jsonl``.

    The CLI's ``argus-skill --notify "<msg>"`` and the cockpit's ``/nudge``
    slash command both append to this file. Each call to the returned
    callable returns one message (or ``None``) and advances a tiny
    offset file so the same line is never replayed twice.
    """
    from ._inbox import drain_inbox_messages

    def _drain_one() -> str | None:
        try:
            from ..skills.stage_machine import current_stage

            messages = drain_inbox_messages(
                life_dir,
                limit=1,
                current_stage=current_stage(project_root or life_dir),
            )
        except Exception:  # noqa: BLE001
            return None
        return messages[0] if messages else None

    return _drain_one


def _pending_question_resolver_for(project_root: Path):
    """Bind daemon inbox replies to the authoritative Manager answer path."""
    state_root = Path(project_root)
    if state_root.parent.name != "projects":
        return None
    sid = state_root.name
    global_root = state_root.parent.parent

    def _resolve(_item: Any, text: str) -> dict[str, Any] | None:
        from ..webapi.manager_bridge import manager_message

        return manager_message(
            sid,
            text,
            global_root=global_root,
        )

    return _resolve


def _resolve_runner_backend_name(
    args: argparse.Namespace,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve the CLI backend name for a ``_SkillLoopRunner``'s default backend.

    Precedence: an explicit ``ARGUS_SKILL_RUNNER_BACKEND`` env override wins;
    otherwise fall back to the backend the CALLER already resolved into
    ``args.backend``. ``core.knobs.resolve_role_backend`` walks the FULL chain —
    role env → shared env → persisted ``/backend`` knob → codex — so
    ``args.backend`` already encodes the operator's choice. Reading the env var
    ALONE misses the persisted knob: the 7×24 daemon exports the env before it
    spawns, so it was unaffected, but the IN-PROCESS Manager front-door (web
    cockpit bridge) resolves e.g. copilot into ``args.backend`` WITHOUT
    exporting the env var. Env-only reads therefore silently fell back to codex
    and spawned ``codex exec`` against an Azure endpoint a copilot operator never
    configured (401 ``Reconnecting… n/100`` retry storm → the front-door lock is
    held for minutes → the cockpit shows "couldn't reach Argus: fetch failed").

    ``None`` → let ``AgentCliBackend`` apply its own codex default (matches the
    prior env-unset behaviour for the ``memory``/unknown case).
    """
    env_map = env if env is not None else os.environ
    explicit = str(env_map.get("ARGUS_SKILL_RUNNER_BACKEND", "") or "").strip()
    if explicit:
        return explicit
    resolved = getattr(args, "backend", None)
    if resolved in ("codex", "claude", "copilot", "opencode"):
        return resolved
    return None


def _resolve_role_runner_backend_name(
    role: str,
    default_backend: str | None,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve one role override while preserving the caller's shared default."""
    from ..core.knobs import resolve_knob

    env_map = env if env is not None else os.environ
    role_var = f"ARGUS_SKILL_{role.upper()}_BACKEND"
    for name in (
        role_var,
        "ARGUS_SKILL_RUNNER_BACKEND",
        "ARGUS_SKILL_LIFE_BACKEND",
    ):
        explicit = str(env_map.get(name, "") or "").strip()
        if explicit:
            return explicit
    return resolve_knob(
        role_var,
        str(default_backend or "codex"),
        env={},
    ).value


def build_life_runner(args: argparse.Namespace, *, seed_thread_id: str | None = None):
    """Return a ``_MissionRunner``-shaped adapter for the requested backend."""
    if args.backend == "memory":
        runner = _MemoryRunner()
        runner.workdir = (
            Path(args.workdir).expanduser() if getattr(args, "workdir", None) else Path.cwd()
        )
        scripted_backend = _ScriptedPlannerBackend.from_env()
        if scripted_backend is not None:
            runner.backend = scripted_backend
        return runner
    if args.backend in ("codex", "claude", "copilot", "opencode"):
        # These are agent-CLI backends: _SkillLoopRunner drives the selected
        # CLI via AgentCliBackend (per-role resolution), so the
        # SAME runner serves every backend. Gating this on "codex" alone used to
        # SystemExit the Manager front-door (triage / web bridge) whenever
        # the operator ran on copilot/claude — the daemon already runs missions
        # on those backends through this very runner.
        #
        # Lazy proxy: ``_SkillLoopRunner`` is defined on the ``_runtime``
        # facade module (composed from this mixin plus the execute/
        # stage-transition mixins), not here, and tests monkeypatch it
        # directly on that module object (see
        # tests/team/test_teammate_entry.py). Importing it at call time
        # resolves against whatever is currently installed on ``_runtime``
        # and avoids a circular import at module-load time.
        from ._runtime import _SkillLoopRunner

        return _SkillLoopRunner(args, seed_thread_id=seed_thread_id)
    raise SystemExit(f"unknown backend: {args.backend}")


# ---------------------------------------------------------------------------
# Supervisor driver (used by both `life run` and chat-mode free text)
# ---------------------------------------------------------------------------
