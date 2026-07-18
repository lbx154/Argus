"""Lifetime-agent runtime infrastructure (backend-neutral).

This module owns the non-interactive machinery shared by the daemon, teammate
runner, and Manager front-door:

- ``build_life_runner``        — factory for memory / codex backends.
- ``run_life_supervisor``      — non-interactive driver (drain a backlog
                                  without a TTY).
- ``_invoke_supervisor``       — assemble a runtime context + run the
                                  supervisor for a single backend.
- ``LifeStderrSink``           — chat-style event renderer (shared with
                                  telegram.notifier and the daemon).
- ``_inbox_drainer_for``       — operator-inbox drain callable.
- the runner adapters (``_MemoryRunner`` / ``_ScriptedPlannerBackend`` /
  ``_SkillLoopRunner``) and the duck-typed ``_Outcome`` they return.

The infrastructure below is intentionally independent of the Ink/Web
presentation layer so daemon and teammate paths never import a terminal UI.
"""
from __future__ import annotations

import argparse
import logging
import os
import shlex
import signal
import sys
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, ClassVar, Protocol

from ..core import paths as core_paths  # noqa: F401 — re-exported convenience
from ..core.knobs import resolve_role_model, resolve_role_reasoning_effort
from ..core.mission_budget import (
    build_mission_budget_guard as _budget_reason_provider,
)
from ..core.ports import EventSink
from ..core.run_gateway import run_exec as gateway_run_exec
from ..engineer.runner import should_clear_thread_id_after_outcome
from ..life import BacklogItem  # noqa: F401 — re-exported convenience
from ..life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)
from ._env import env_flag as _env_flag
from ._env import env_int as _env_int
from ._runtime_backends import (
    _TEST_DAEMON_PLANNER_SCRIPT_ENV,
    _MemoryRunner,
    _Outcome,
    _ScriptedPlannerBackend,
)
from ._self_reply import SelfReplyMixin
from ._self_reply import (
    self_retryable_transport_failure as _self_retryable_transport_failure,
)
from ._target_paths import resolve_life_root

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env helpers + memory protocols (formerly _life_repl/_base.py)
# ---------------------------------------------------------------------------


class _CommonMemory(Protocol):
    @property
    def identity(self) -> Any: ...

    @property
    def journal(self) -> Any: ...

    @property
    def backlog(self) -> Any: ...


class _SplitMemory(_CommonMemory, Protocol):
    @property
    def global_mem(self) -> Any: ...

    @property
    def project(self) -> Any: ...

    @property
    def global_root(self) -> Any: ...

    def render_prelude(self) -> str: ...


def _memory_project_root(mem: Any) -> Path:
    project = getattr(mem, "project", None)
    root = getattr(project, "root", None)
    if root is not None:
        return Path(root)
    return Path(getattr(mem, "root"))


def _memory_global_root(mem: Any) -> Path:
    root = getattr(mem, "global_root", None)
    if root is not None:
        return Path(root)
    return _memory_project_root(mem)


def _resolve_global_root(args: argparse.Namespace) -> Path:
    return resolve_life_root(getattr(args, "life_dir", None))


def _project_state_dir_for(args: argparse.Namespace, workdir: Path) -> Path | None:
    """Resolve the existing per-project runtime state directory."""
    if not _env_flag("ARGUS_SKILL_CHECKPOINT_PERSIST", True):
        return None
    try:
        explicit_state_dir = getattr(args, "project_state_dir", None)
        if explicit_state_dir:
            state_dir = Path(explicit_state_dir).expanduser()
            state_dir.mkdir(parents=True, exist_ok=True)
            return state_dir

        from ..core.project import project_fingerprint

        global_root = _resolve_global_root(args)
        fingerprint = project_fingerprint(workdir).fingerprint
        state_dir = global_root / "projects" / fingerprint
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir
    except Exception:  # noqa: BLE001 — never let path resolution break a mission
        return None


def _checkpoint_path_for(args: argparse.Namespace, workdir: Path) -> Path | None:
    """Shared checkpoint in internal project state, never the output workdir."""
    if not _env_flag("ARGUS_SKILL_CHECKPOINT_PERSIST", True):
        return None
    try:
        state_dir = _project_state_dir_for(args, workdir)
        return state_dir / "CHECKPOINT.md" if state_dir is not None else None
    except Exception:  # noqa: BLE001 — never let path resolution break a mission
        return None


class LifeStderrSink:
    """Forward events to stderr using chat's renderer.

    Always-verbose: every event type the engine emits (except a small
    in-life silence-list below) is shown. The product positioning is a
    7×24 lifetime agent — operators want full visibility of what the
    daemon is doing, always. The earlier ``verbose``/``quiet`` toggles
    have been removed (kept ``quiet`` only for in-process tests that
    pump events without wanting stderr noise).
    """

    def __init__(self, *, quiet: bool = False) -> None:
        self.quiet = quiet
        self._render: Callable[..., str] | None = None
        self._theme: Any = None
        try:
            from ..cli import default_theme, render_event_for_terminal
            self._render = render_event_for_terminal
            self._theme = default_theme()
        except Exception:  # noqa: BLE001
            pass

    def _allowed(self, event_type: str) -> bool:  # noqa: ARG002
        return True

    # Events that life.mission.started/completed already cover; we silence
    # them in life mode to avoid duplicate noise around mission boundaries.
    # Also drop a few protocol/skill-machinery events that the user can't
    # act on and that just clutter the chat scroll (matcher/author
    # banter, internal "distill done" weight reports).
    _SILENCED_IN_LIFE: ClassVar[frozenset[str]] = frozenset({
        "loop.start",
        "loop.done",
        "match.info",         # "skill store empty - will distill a new playbook"
        "distill.done",       # "distilled (4009 chars, 0 tok)"
    })

    def handle_event(self, event: dict[str, Any]) -> None:
        if self.quiet:
            return
        et = str(event.get("type", ""))
        if et in self._SILENCED_IN_LIFE:
            return
        if not self._allowed(et):
            return
        if self._render is not None:
            try:
                line = self._render(event, theme=self._theme)
                if line:  # empty string = renderer chose to swallow event
                    sys.stderr.write(line + "\n")
                    sys.stderr.flush()
                return
            except Exception:  # noqa: BLE001
                pass
        text = event.get("text") or event.get("title") or ""
        sys.stderr.write(f"[{et}] {text}\n")
        sys.stderr.flush()

    def handle_stream_line(self, stream: str, line: str) -> None:  # noqa: ARG002
        """Required by ``make_stream_progress_callback``.

        Life mode has no JSONL outbox to keep an audit trail in — the
        cooked ``engineer.progress`` events that ``stream_progress``
        synthesises from the same raw lines are what we render. The raw
        lines themselves are intentionally discarded here; ``codex
        --output-format stream-json`` produces dozens per second and
        echoing them all would defeat the point of having a renderer.
        """
        return

    def close(self) -> None:
        return


def _should_run_stage_transition(
    status: object,
    planner_report: dict | None = None,
) -> bool:
    normalized = str(status or "")
    stage_reconciliation = bool(
        isinstance(planner_report, dict)
        and planner_report.get("stage_reconciliation_required") is True
    )
    return (
        stage_reconciliation
        or (
            normalized != "replan_requested"
            and not normalized.startswith("paused_")
        )
    )




class _SkillLoopRunner(SelfReplyMixin):
    """Runs each mission through a fresh ``SkillLoop`` (codex backend).

    Bypasses the ``ARGUS_SKILL_BACKEND`` env var: when life mode
    selects ``codex`` that's the user's explicit ask, so we always
    construct a real ``AgentCliBackend``. This was a real bug —
    previously the backend silently fell back to memory when the env
    var was unset, while the UI happily printed ``backend: codex``.
    """

    def __init__(self, args: argparse.Namespace, *, seed_thread_id: str | None = None) -> None:
        from ..loop import SkillLoop, SkillLoopConfig

        self._SkillLoop = SkillLoop
        self._SkillLoopConfig = SkillLoopConfig
        try:
            from ..adapters.agent_cli_backend import AgentCliBackend
        except ImportError as exc:  # pragma: no cover — depends on optional install
            raise SystemExit(
                f"Codex backend requested but ArgusBot is unavailable: {exc}.\n"
                "Install the codex extra: `pip install 'argus-skill[codex]'`."
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
                self._stream_progress_relay(
                    sink, self._current_failure_ledger, stream, line
                )
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
        raw_extra = os.environ.get("ARGUS_SKILL_RUNNER_EXTRA_ARGS", "").strip()
        extra = _strip_legacy_codex_profile_args(
            shlex.split(raw_extra) if raw_extra else None
        )
        stop_event = getattr(args, "stop_event", None)
        # Set ONLY by the real 7×24 daemon's own namespace builder (see
        # ``daemon/life_worker.py:_runner_namespace``) — never by the
        # front-door quick-reply runner
        # or by the test/legacy ``_invoke_supervisor`` path. This is what
        # lets the Manager (running in the operator-facing API process)
        # ask the daemon to abort whatever mission it is currently executing:
        # the request is a small file in the shared life_dir (see
        # ``tools.mission_control``), and only the runner that is actually
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
                from ..tools.mission_control import pop_pending_mission_abort

                abort_reason = pop_pending_mission_abort(
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
                "ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS", 0,
            ),
            default_watchdog_hard_idle_seconds=_env_int(
                "ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS", 3600,
            ),
            event_callback=_trampoline,
        )
        # Expose the underlying backend so the LifeSupervisor's
        # iteration loop can drive a Critic agent through it without
        # building a second codex process.
        self.backend = self._backend

        # Per-role backends. Each agent role (engineer / reviewer / planner /
        # manager) can be pinned to its OWN backend via
        # ``ARGUS_SKILL_{ROLE}_BACKEND`` (codex / claude / copilot) plus an
        # optional ``ARGUS_SKILL_{ROLE}_RUNNER_BIN``. When neither is set the
        # role SHARES the single default backend above — so the common case
        # still builds exactly one CLI process and behaviour is unchanged. Set
        # an override only when you want, e.g., the reviewer on a different
        # provider than the engineer.
        def _role_backend(role: str):
            role_backend_name = _resolve_role_runner_backend_name(
                role, backend_name,
            )
            bin_env = os.environ.get(
                f"ARGUS_SKILL_{role.upper()}_RUNNER_BIN", ""
            ).strip()
            from ..agent_cli.runner_backend import (
                default_runner_bin,
                normalize_runner_backend,
            )

            chosen = normalize_runner_backend(role_backend_name)
            same_type = normalize_runner_backend(backend_name) == chosen
            if same_type and not bin_env:
                return self._backend
            role_bin = bin_env or (
                runner_bin if same_type else default_runner_bin(chosen)
            )
            return AgentCliBackend(
                backend=chosen,
                runner_bin=role_bin,
                default_extra_args=extra,
                default_interrupt_reason_provider=(
                    _stop_reason if stop_event is not None else None
                ),
                default_watchdog_soft_idle_seconds=_env_int(
                    "ARGUS_SKILL_RUNNER_SOFT_IDLE_SECONDS", 0,
                ),
                default_watchdog_hard_idle_seconds=_env_int(
                    "ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS", 3600,
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
        self._usage_project_root = (
            Path(raw_usage_root).expanduser() if raw_usage_root else None
        )
        raw_global_root = str(getattr(args, "global_root", "") or "").strip()
        self._usage_global_root = (
            Path(raw_global_root).expanduser() if raw_global_root else None
        )
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
            Path(args.workdir).expanduser()
            if getattr(args, "workdir", None)
            else Path.cwd()
        )
        _manager_session_root = (
            Path(getattr(args, "manager_session_root")).expanduser()
            if getattr(args, "manager_session_root", None)
            else _manager_workdir
        )
        # ``_artifact_root`` / Manager's ``project_root`` MUST be the real
        # mission WORKDIR, never the daemon's internal life_dir: every OTHER
        # reader/writer of ``research/PIPELINE_STATE.json`` (stage_checklists.
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
            from ..skills.store import SkillStore

            # A default config is enough for the matcher: ``resolved_matcher_model``
            # already applies the ``ARGUS_SKILL_MATCHER_MODEL`` env override, and
            # ``matcher_reasoning_effort`` defaults to the same value the SkillLoop
            # uses. We only need the matcher knobs here, not the full mission cfg.
            cfg = SkillLoopConfig()
            return SkillStore(
                Path(args.skills_dir),
                runner=self.manager_backend or self._backend,
                matcher_model=cfg.resolved_matcher_model(),
                matcher_reasoning_effort=cfg.matcher_reasoning_effort,
            )
        except Exception:  # noqa: BLE001 — never block start-up on the matcher
            log.debug("manager skill store build skipped", exc_info=True)
            return None

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
        """The distinct role AgentCliBackend instances this runner drives (each
        appears once), that support the source-level budget guard."""
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
            if be is not None and id(be) not in seen and hasattr(be, "set_budget_reason_provider"):
                seen.add(id(be))
                out.append(be)
        return out

    def _set_budget_guard(self, provider) -> list:
        """Install ``provider`` (or clear with ``None``) as the per-mission budget
        guard on every role backend; returns the backends it touched so the caller
        can clear them in a ``finally``."""
        backends = self._distinct_backends()
        for be in backends:
            try:
                be.set_budget_reason_provider(provider)
            except Exception:  # noqa: BLE001 — a guard-set fault must never fail the mission
                pass
        return backends

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

    def execute(
        self,
        *,
        objective: str,
        original_objective: str = "",
        sink: EventSink,
        preload_injects: list[str] | None = None,  # noqa: ARG002 — protocol parity
        prelude_context: str = "",
        seed_thread_id: str | None = None,
        scope: str = "",
        per_mission_budget: Any | None = None,
        preplanned: bool = False,
        mission_id: str | None = None,
        usage_mission_id: str | None = None,
        max_rounds_override: int | None = None,
        workflow_mode_override: str = "",
        require_independent_review: bool = False,
    ) -> _Outcome:
        # Chat fast-path (operator-front-door-only; gated by _allow_chat_fast_path).
        # The classifier + reply logic lives in ``_maybe_chat_outcome``; here we
        # only gate it so the 7×24 daemon (``_allow_chat_fast_path=False``) does
        # not classify arbitrary autonomous work — agent-produced backlog work
        # must not be second-guessed.
        if self._allow_chat_fast_path:
            self._set_usage_context(usage_mission_id or mission_id)
            try:
                _chat = self._maybe_chat_outcome(
                    objective=objective,
                    sink=sink,
                    seed_thread_id=seed_thread_id,
                )
            finally:
                self._set_usage_context(None)
            if _chat is not None:
                return _chat

        args = self._args
        # 7×24 product: default to dangerous_yolo (no bwrap sandbox).
        # The operator runs the daemon on their own box and explicitly
        # consents to autonomous execution; the sandbox only fights us
        # (`bwrap: Can't create file at /.codex: Permission denied`).
        # Operators can opt back into sandbox via ARGUS_SKILL_SAFE_MODE=1.
        safe_mode = _env_flag("ARGUS_SKILL_SAFE_MODE", False)
        config_kwargs = {
            "engineer_model": args.engineer_model,
            "reviewer_model": args.reviewer_model,
            "engineer_initial_reasoning_effort": os.environ.get(
                "ARGUS_SKILL_ENGINEER_INITIAL_REASONING_EFFORT", "high"
            ),
            "engineer_reasoning_effort": getattr(
                args, "engineer_reasoning_effort", "xhigh"
            ),
            "reviewer_reasoning_effort": getattr(
                args,
                "reviewer_reasoning_effort",
                "xhigh",
            ),
            "max_rounds": (
                max(1, int(max_rounds_override))
                if max_rounds_override is not None
                else args.max_rounds
            ),
            "skill_ops_enabled": _env_flag(
                "ARGUS_SKILL_SKILL_OPS",
                default=True,
            ),
            "wiki_ops_enabled": _env_flag(
                "ARGUS_SKILL_WIKI_OPS",
                default=True,
            ),
            "auto_init_wiki": _env_flag(
                "ARGUS_SKILL_AUTO_INIT_WIKI",
                default=True,
            ),
            "auto_compact_enabled": _env_flag(
                "ARGUS_SKILL_AUTO_COMPACT",
                # Compaction is an explicit maintenance operation, not part of
                # every mission close. Per-mission sweeps scale with the entire
                # shared library and historically regenerated/archived the same
                # duplicates in a costly loop.
                default=False,
            ),
            "dangerous_yolo": not safe_mode,
            "full_auto": safe_mode,
            "skip_git_repo_check": True,
            "engineer_self_review_enabled": (
                _env_flag("ARGUS_SKILL_ENGINEER_SELF_REVIEW", default=True)
                and not require_independent_review
            ),
            # Filled from the resolved vertical below.  Fail-safe default: an
            # undecided task is bounded/non-paper.
            "paper_mission": False,
            # Shared Markdown checkpoint in internal project state. Engineer
            # and Reviewer receive its absolute path and edit it in sequence;
            # output workdirs contain deliverables only.
            "checkpoint_path": _checkpoint_path_for(
                args,
                Path(args.workdir).expanduser() if args.workdir else Path.cwd(),
            ),
            "session_id": mission_id,
            # Process-correctness audit: the reviewer runs in the project
            # work-tree and only sees the engineer's final summary. Give it the
            # ABSOLUTE path to this project's engineer execution log
            # (``<life_dir>/events.jsonl``) so it can grep HOW the result was
            # produced. This runtime log remains outside the worktree.
        }
        _project_state_dir = _project_state_dir_for(
            args, Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        )
        config_kwargs["engineer_log_path"] = (
            str(_project_state_dir / "events.jsonl")
            if _project_state_dir is not None
            else ""
        )
        # Campaign lifetime metadata forwarded from the daemon namespace so the
        # Manager stage hook receives open_ended=True for daemon-created open-ended
        # campaigns, preventing final_stage_completion_decision from overwriting a
        # structured Manager rollback verdict with a bounded completion.
        config_kwargs["open_ended"] = bool(getattr(args, "open_ended", False))
        config_kwargs["continuous_objective"] = str(
            getattr(args, "continuous_objective", "") or ""
        )
        # A paper contract is enabled only by a positively resolved
        # ``full_paper`` vertical.  An explicit False from a specialized caller
        # may still opt out; True cannot turn a non-paper vertical into a paper.
        _proot = Path(
            getattr(self, "_artifact_root", None)
            or (Path(args.workdir).expanduser() if args.workdir else Path.cwd())
        )
        _paper_override = getattr(args, "paper_mission", None)
        _paper_allowed = True if _paper_override is None else bool(_paper_override)
        config_kwargs["paper_mission"] = (
            _paper_allowed and _paper_mission_for_project_root(_proot)
        )
        config_kwargs["workflow_mode"] = (
            workflow_mode_override.strip().lower()
            or _workflow_mode_for_project_root(_proot)
        )
        try:
            from inspect import signature

            sig = signature(self._SkillLoopConfig)
            if not any(
                param.kind == param.VAR_KEYWORD for param in sig.parameters.values()
            ):
                config_kwargs = {
                    key: value
                    for key, value in config_kwargs.items()
                    if key in sig.parameters
                }
        except (TypeError, ValueError):
            pass
        config = self._SkillLoopConfig(**config_kwargs)
        workdir = (
            Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        )
        # The per-project runtime state dir holds inbox.jsonl + events.jsonl.
        operator_state_dir = _project_state_dir_for(args, workdir)
        # REAL operator inbox (Change A): drain queued ``--notify`` / ``/nudge``
        # messages EACH engineer round — not just at mission start — so the
        # operator can steer a long in-flight mission instead of being locked out
        # until the next mission. Wired through the existing per-round
        # ``extra_guidance_provider`` hook; shares ``inbox.offset`` with the
        # supervisor's mission-start drain, so each message is delivered exactly
        # once with no duplication. Never raises into a mission.
        inbox_life_dir = operator_state_dir

        def _inbox_guidance_provider() -> list[str]:
            msgs: list[str] = []
            if inbox_life_dir is not None:
                try:
                    from ..skills.stage_checklists import current_stage
                    from ._inbox import drain_inbox_messages

                    msgs.extend(drain_inbox_messages(
                        inbox_life_dir,
                        current_stage=current_stage(workdir),
                    ))
                except Exception:  # noqa: BLE001 — never break a mission
                    pass
            return msgs

        extra_guidance_provider = (
            _inbox_guidance_provider
            if inbox_life_dir is not None
            else None
        )
        engineer_backend = getattr(self, "engineer_backend", None) or self._backend
        global_skills_dir = Path(args.skills_dir)
        skill_store = None
        project_state_dir = str(getattr(args, "project_state_dir", "") or "").strip()
        if project_state_dir:
            from ..skills.layered import LayeredSkillStore

            skill_store = LayeredSkillStore(
                project_dir=Path(project_state_dir) / "skills",
                global_dir=global_skills_dir,
                runner=engineer_backend,
                matcher_model=config.resolved_matcher_model(),
                matcher_reasoning_effort=config.matcher_reasoning_effort,
            )
        loop = self._SkillLoop(
            skills_dir=global_skills_dir,
            engineer_runner=engineer_backend,
            reviewer_runner=getattr(self, "reviewer_backend", None) or self._backend,
            config=config,
            skill_store=skill_store,
            on_event=sink.handle_event,
            extra_guidance_provider=extra_guidance_provider,
        )
        full_task = objective
        if prelude_context:
            full_task = f"{prelude_context}\n---\n## Live objective\n{objective}"
        # Use the seed for the first execute() of this runner; subsequent
        # execute() calls (LifeSupervisor may run several missions in one
        # supervisor.run()) chain off the previous mission's last thread_id.
        seed = self._next_seed_thread_id if seed_thread_id is None else seed_thread_id
        self._current_sink = sink
        self._current_failure_ledger = None
        # Scope is threaded structurally from the planner via the backlog
        # item's tags (LifeSupervisor passes _planner_scope_from_item(item)).
        # We no longer re-parse it out of the objective prose — the harness
        # should consume the structured field, not sniff the rendered text.
        mission_scope = (scope or "").strip().lower()
        # SOURCE-LEVEL budget cap: gate EVERY LLM call this mission makes on the
        # live per-mission spend. Set the guard on the role backends for the
        # duration of the mission and clear it in the finally so it can never leak
        # into a later mission. ``None`` budget → no guard (cap unenforced, as before).
        self._set_usage_context(usage_mission_id or mission_id)
        _guarded = self._set_budget_guard(_budget_reason_provider(per_mission_budget))
        try:
            # User-authored bounded work now follows the full team chain:
            # Manager → Planner → Engineer → Reviewer. Planner-authored backlog
            # items set ``preplanned=True`` and skip this call, avoiding a second
            # redundant planning pass. The plan is advisory context, not a gate:
            # if drafting fails, Engineer still receives the immutable objective.
            if (
                not preplanned
                and getattr(config, "workflow_mode", "staged") != "direct"
            ):
                try:
                    from ..manager.plan_mode import draft_plan
                    from ..skills.vertical_select import resolve_vertical
                    from ..verticals._base import (
                        load_vertical,
                        vertical_role_banner,
                    )

                    active_vertical = resolve_vertical(workdir)
                    vertical_module = load_vertical(
                        active_vertical,
                        project_root=workdir,
                    )
                    planner_role_banner = vertical_role_banner(
                        vertical_module,
                        "planner",
                    )
                    plan = draft_plan(
                        getattr(self, "planner_backend", None) or self._backend,
                        original_objective or objective,
                        sink=sink,
                        model=getattr(args, "plan_model", None),
                        reasoning_effort=resolve_role_reasoning_effort(
                            "ARGUS_SKILL_PLANNER_REASONING_EFFORT"
                        ),
                        run_label="planner-bounded-plan",
                        role_banner=planner_role_banner,
                    )
                    if plan.steps:
                        lines = ["## Planner execution plan (advisory)"]
                        for index, step in enumerate(plan.steps, 1):
                            detail = f" — {step.detail}" if step.detail else ""
                            lines.append(f"{index}. {step.title}{detail}")
                        if plan.notes:
                            lines.append("Notes: " + "; ".join(plan.notes))
                        full_task += "\n\n---\n" + "\n".join(lines)
                        sink.handle_event({
                            "type": "plan.completed",
                            "agent_layer": "planner",
                            "plan_mode": "bounded",
                            "steps": len(plan.steps),
                            "text": f"bounded execution plan · {len(plan.steps)} steps",
                        })
                    else:
                        sink.handle_event({
                            "type": "life.planner.error",
                            "agent_layer": "planner",
                            "error": plan.error or "bounded plan unavailable",
                            "text": plan.error or "bounded plan unavailable; Engineer continues",
                        })
                except Exception as exc:  # noqa: BLE001 — planning is advisory
                    sink.handle_event({
                        "type": "life.planner.error",
                        "agent_layer": "planner",
                        "error": f"{type(exc).__name__}: {exc}",
                        "text": "bounded plan unavailable; Engineer continues",
                    })
            outcome = loop.run(
                full_task, workdir=workdir, seed_thread_id=seed,
                objective_for_skill=objective,
                original_objective=original_objective or objective,
                scope=mission_scope,
                per_mission_budget=per_mission_budget,
            )
        finally:
            self._current_sink = None
            self._current_failure_ledger = None
            for _be in _guarded:
                try:
                    _be.set_budget_reason_provider(None)
                except Exception:  # noqa: BLE001 — clearing the guard must never fail the mission
                    pass
            self._set_usage_context(None)
        new_tid = getattr(outcome, "last_thread_id", None)
        if should_clear_thread_id_after_outcome(
            status=str(getattr(outcome, "status", "")),
            fatal_error=str(getattr(outcome, "stop_reason", "") or ""),
            stop_kind=getattr(outcome, "stop_kind", None),
        ):
            self.last_thread_id = None
            self._next_seed_thread_id = None
            new_tid = None
        elif new_tid:
            self.last_thread_id = new_tid
            self._next_seed_thread_id = new_tid
        auth_fail = self._consume_auth_failure()
        # Reviewer completion contract: certify whole-project completion only
        # from the final reviewer verdict (never raw success). Fail-closed:
        # absent rounds / review / non-final scope ⇒ not certified.
        final_submission_certified = False
        completion_evidence = ""
        # Pull the reviewer's structured planner briefing off the final round
        # so the supervisor can journal it for the project planner verbatim.
        planner_report: dict = {}
        checklist_feedback: dict = {}
        step_back: dict | None = None
        operator_question = ""
        research_result: dict = {}
        rounds_list = getattr(outcome, "rounds", None) or []
        if rounds_list:
            _final_review = getattr(rounds_list[-1], "review", None)
            if _final_review is not None:
                report = getattr(_final_review, "planner_report", None)
                if isinstance(report, dict):
                    planner_report = report
                _cfb = getattr(_final_review, "checklist_feedback", None)
                if isinstance(_cfb, dict) and _cfb:
                    checklist_feedback = _cfb
                _sb = getattr(_final_review, "step_back", None)
                if isinstance(_sb, dict) and _sb:
                    step_back = _sb
                operator_question = str(
                    getattr(_final_review, "operator_question", "") or ""
                ).strip()
                _research_result = getattr(_final_review, "research_result", None)
                if isinstance(_research_result, dict):
                    research_result = dict(_research_result)
        if mission_scope == "final_submission":
            final_review = None
            if rounds_list:
                final_review = getattr(rounds_list[-1], "review", None)
            if final_review is not None and getattr(
                final_review, "final_submission_certified", False
            ):
                final_submission_certified = True
                completion_evidence = (
                    getattr(final_review, "completion_summary_markdown", "")
                    or getattr(final_review, "reason", "")
                )
        # STAGE AUTHORITY: the Manager is the SOLE post-bootstrap writer of the
        # pipeline stage. After this round's reviewer verdict, the Manager makes
        # its OWN judgment (advance / hold / rollback) and writes
        # PIPELINE_STATE.json. See ``_decide_stage_transition``.
        effective_status = str(outcome.status)
        effective_stop_kind = getattr(outcome, "stop_kind", None)
        effective_recoverable = bool(getattr(outcome, "recoverable", False))
        effective_reason = outcome.reason or ""
        stage_transition: dict = {}
        if (
            getattr(config, "workflow_mode", "staged") != "direct"
            and _should_run_stage_transition(effective_status, planner_report)
        ):
            stage_budget_exhausted = bool(
                per_mission_budget is not None
                and per_mission_budget.exceeded()
            )
            if not stage_budget_exhausted:
                self._current_sink = sink
                self._set_usage_context(usage_mission_id or mission_id)
                stage_guarded = self._set_budget_guard(
                    _budget_reason_provider(per_mission_budget)
                )
                try:
                    stage_transition = self._decide_stage_transition(
                        rounds_list=rounds_list,
                        workdir=workdir,
                        sink=sink,
                        root_task_id=usage_mission_id or mission_id,
                        mission_scope=mission_scope,
                        open_ended=bool(getattr(config, "open_ended", False)),
                        continuous_objective=str(
                            getattr(config, "continuous_objective", "") or ""
                        ),
                    )
                finally:
                    self._current_sink = None
                    for backend in stage_guarded:
                        try:
                            backend.set_budget_reason_provider(None)
                        except Exception:  # noqa: BLE001
                            pass
                    self._set_usage_context(None)
            else:
                effective_status = "paused_budget"
                effective_stop_kind = "budget_exhausted"
                effective_recoverable = True
                effective_reason = "per-mission budget exhausted before Manager stage decision"
        return _Outcome(
            success=bool(outcome.successful and effective_status == "done"),
            status=effective_status,
            stop_reason=effective_reason,
            stop_kind=effective_stop_kind,
            recoverable=effective_recoverable,
            rounds=outcome.round_count,
            matched_skill_name=outcome.skill_used,
            skill_distilled=outcome.skill_distilled,
            last_thread_id=new_tid,
            auth_failure=auth_fail,
            final_submission_certified=final_submission_certified,
            completion_evidence=completion_evidence,
            planner_report=planner_report,
            checklist_feedback=checklist_feedback,
            step_back=step_back,
            stage_transition=stage_transition,
            operator_question=operator_question,
            research_result=research_result,
        )

    def _decide_stage_transition(
        self,
        *,
        rounds_list: list,
        workdir: Path,
        sink: EventSink,
        root_task_id: str | None = None,
        mission_scope: str = "",
        open_ended: bool = False,
        continuous_objective: str = "",
    ) -> dict:
        """Hand this round's reviewer verdict to the Manager — the SOLE
        post-bootstrap writer of the pipeline stage — and let it judge
        advance / hold / rollback and write ``PIPELINE_STATE.json``.

        Reviewer/planner only advise; the engineer no longer edits stage state.
        Fail-open: a stage decision must NEVER break a mission — any error
        degrades to a no-op (the stage simply stays put this round). Returns the
        decision dict (empty on skip/error) for the ``_Outcome`` / journal; the
        stage write itself already happened inside ``decide_stage_transition``.
        """
        try:
            from ..manager import Manager

            final_review = (
                getattr(rounds_list[-1], "review", None) if rounds_list else None
            )
            st = Manager(
                project_root=getattr(self, "_artifact_root", workdir),
                runner=getattr(self, "manager_backend", None) or self._backend,
                skill_store=getattr(self, "_manager_skill_store", None),
                manager_session_root=getattr(self, "_manager_session_root", workdir),
                usage_context=self.task_usage_context,
            ).decide_stage_transition(
                review=final_review,
                project_root=getattr(self, "_artifact_root", workdir),
                on_event=sink.handle_event,
                root_task_id=root_task_id,
                mission_scope=mission_scope,
                open_ended=open_ended,
                continuous_objective=continuous_objective,
            )
            decision = {
                "action": st.action,
                "target_stage": st.target_stage,
                "reason": st.reason,
                "current_stage": st.current_stage,
                "source": st.source,
                "diagnostic": st.diagnostic,
            }
            sink.handle_event({"type": "life.manager.stage_decision", **decision})
            return decision
        except Exception:  # noqa: BLE001 — stage decision must never break a mission
            log.debug("manager stage decision skipped", exc_info=True)
            return {}

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
        return f"{theme.bold('life')}    " + theme.yellow("no daemon") + theme.dim(
            " — tasks queue until `argus-skill --daemon`"
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
    that ArgusBot or the configured CLI binary are missing. Best-effort: if
    anything raises we stay quiet — a confusing warning is worse than no
    warning, and the real failure path (``_SkillLoopRunner``) will
    print a precise error when a mission actually starts.
    """
    try:
        from ..adapters.agent_cli_backend import _import_argusbot
    except ImportError:
        return ("bundled agent_cli module not importable — "
                "reinstall argus-skill")
    try:  # noqa: SIM105
        _import_argusbot()
    except Exception:  # noqa: BLE001
        return ("bundled agent_cli failed to load — "
                "check the argus-skill install")
    import shutil

    # BUG FIX: this used to hardcode `shutil.which("codex")` regardless of
    # which CLI is actually configured, so an operator running entirely on
    # ARGUS_SKILL_RUNNER_BACKEND=claude/copilot (no `codex` npm package
    # installed at all, by design) got a false "codex binary not found"
    # warning on every banner / `/doctor` run. Check whichever backend is
    # actually configured; "codex" (the default) keeps its exact original
    # message for backward compatibility.
    from ..core.knobs import resolve_role_backend

    backend = resolve_role_backend("")
    bin_path = os.environ.get("ARGUS_SKILL_RUNNER_BIN") or shutil.which(backend)
    if not bin_path:
        if backend == "codex":
            hint = "install with `npm install -g @openai/codex`"
        else:
            hint = f"install the `{backend}` CLI"
        return (f"`{backend}` binary not found on PATH — {hint} "
                f"or set ARGUS_SKILL_RUNNER_BIN")
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
            from ..skills.stage_checklists import current_stage

            messages = drain_inbox_messages(
                life_dir,
                limit=1,
                current_stage=current_stage(project_root or life_dir),
            )
        except Exception:  # noqa: BLE001
            return None
        return messages[0] if messages else None

    return _drain_one


def _resolve_runner_backend_name(
    args: argparse.Namespace, env: Mapping[str, str] | None = None,
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
    if resolved in ("codex", "claude", "copilot"):
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
            Path(args.workdir).expanduser()
            if getattr(args, "workdir", None)
            else Path.cwd()
        )
        scripted_backend = _ScriptedPlannerBackend.from_env()
        if scripted_backend is not None:
            runner.backend = scripted_backend
        return runner
    if args.backend in ("codex", "claude", "copilot"):
        # All three are agent-CLI backends: _SkillLoopRunner drives the codex /
        # claude / copilot CLI via AgentCliBackend (per-role resolution), so the
        # SAME runner serves every backend. Gating this on "codex" alone used to
        # SystemExit the Manager front-door (triage / web bridge) whenever
        # the operator ran on copilot/claude — the daemon already runs missions
        # on those backends through this very runner.
        return _SkillLoopRunner(args, seed_thread_id=seed_thread_id)
    raise SystemExit(f"unknown backend: {args.backend}")


# ---------------------------------------------------------------------------
# Supervisor driver (used by both `life run` and chat-mode free text)
# ---------------------------------------------------------------------------

def _paper_mission_for_project_root(project_root: Path | str) -> bool:
    """Return True only for an explicitly resolved paper-shaped vertical.

    Missing/corrupt state is deliberately non-paper.  ``resolve_vertical`` has
    a compatibility fallback to ``research`` for undecided projects; using that
    fallback as a mission-type signal caused ordinary bounded tasks to pay for
    paper idea search and inherit EMNLP guidance. A persisted Manager decision
    is required here.
    """
    try:
        from ..skills.vertical_select import _persisted_vertical
        from ..verticals._base import load_vertical, vertical_completion_gate

        root = Path(project_root).expanduser()
        persisted = _persisted_vertical(root)
        if persisted is None:
            return False
        vertical = persisted
        return (
            vertical_completion_gate(
                load_vertical(vertical, project_root=root)
            )
            == "full_paper"
        )
    except Exception:  # noqa: BLE001 — mission typing must fail safe
        return False


def _workflow_mode_for_project_root(project_root: Path | str) -> str:
    """Resolve the Manager-persisted workflow contract; fail safe to staged."""
    try:
        from ..skills.vertical_select import resolve_workflow_mode

        return resolve_workflow_mode(Path(project_root).expanduser())
    except Exception:  # noqa: BLE001
        return "staged"


def _build_supervisor_config(
    *,
    per_mission_cap_usd: float,
    daily_cap_usd: float,
    global_daily_cap_usd: float,
    once: bool,
    max_missions: int,
    project_worktree: Path | None,
    stop_event: threading.Event,
    project_root: Path,
    artifact_root: Path | None = None,
    runtime_context: str,
    continuous: bool,
    continuous_objective: str,
    open_ended: bool,
) -> LifeSupervisorConfig:
    from ..life.telemetry import telemetry_interval_from_env

    # Mission type follows a positive Manager-authored vertical decision.  An
    # undecided or malformed project is bounded/non-paper, never implicitly an
    # EMNLP campaign.
    paper_mission = _paper_mission_for_project_root(artifact_root or project_root)

    return LifeSupervisorConfig(
        budget=LifeBudget(
            per_mission_cap_usd=per_mission_cap_usd,
            daily_cap_usd=daily_cap_usd,
            global_daily_cap_usd=global_daily_cap_usd,
            max_missions=1 if once else max_missions,
        ),
        poll_interval_seconds=2.0,
        project_worktree=(
            Path(project_worktree).expanduser()
            if project_worktree is not None
            else None
        ),
        stop_event=stop_event,
        user_inbox=_inbox_drainer_for(
            project_root,
            project_root=artifact_root or project_worktree or project_root,
        ),
        runtime_context=runtime_context,
        continuous=continuous,
        continuous_objective=continuous_objective,
        open_ended=open_ended,
        full_paper_gate=paper_mission and open_ended,
        paper_mission=paper_mission,
        telemetry_dir=project_root,
        artifact_root=artifact_root or project_root,
        telemetry_interval_seconds=telemetry_interval_from_env(),
    )


def run_life_supervisor(
    *,
    mem: _SplitMemory,
    runner: Any,
    engineer_model: str,
    reviewer_model: str,
    once: bool,
    max_missions: int,
    per_mission_cap_usd: float,
    daily_cap_usd: float,
    global_daily_cap_usd: float,
    project_worktree: Path | None = None,
    artifact_root: Path | None = None,
    quiet: bool = False,
    runtime_context: str = "",
    continuous: bool = False,
    continuous_objective: str = "",
    open_ended: bool = True,
) -> dict[str, Any]:
    """Run ``LifeSupervisor`` with proper signal-handler save/restore.

    Restoring previous SIGINT/SIGTERM handlers on exit keeps the foreground
    caller's Ctrl-C semantics after a run finishes.
    """
    stop_event = threading.Event()

    def _on_signal(signum: int, _frame: Any) -> None:  # noqa: ANN401
        print(f"\nlife: received signal {signum}, requesting stop", file=sys.stderr)
        stop_event.set()

    prev_int = signal.getsignal(signal.SIGINT)
    prev_term = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        from ..life.event_log import JsonlEventSink
        stderr_sink = LifeStderrSink(quiet=quiet)
        project_root = _memory_project_root(mem)
        sink = JsonlEventSink(stderr_sink, life_dir=project_root)
        # Manager divides the Task first: classify the vertical, split into its
        # Stage template, and COMMIT the choice. The supervisor below then TRUSTS
        # the persisted vertical (life/supervisor/_core.py:2460) and won't
        # re-classify. Missing execution handoffs fail closed so raw operator
        # routing/presentation instructions never reach Planner/Engineer.
        if continuous and str(continuous_objective).strip():
            try:
                # Prefer the runner's single Manager instance (manager backend);
                # fall back to an ad-hoc Manager only when the runner has none
                # (e.g. the memory runner used in tests).
                mgr = getattr(runner, "manager", None)
                if mgr is None:
                    from ..manager import Manager

                    mgr = Manager(
                        project_root=artifact_root or project_root,
                        runner=getattr(runner, "manager_backend", None)
                        or getattr(runner, "backend", None),
                        skill_store=getattr(runner, "_manager_skill_store", None),
                    )
                division = mgr.divide(
                    continuous_objective,
                    ask_on_new_domain=_env_flag("ARGUS_SKILL_DOMAIN_ASK", False),
                )
                # Headless driver: there is no live operator turn here, so an
                # ask-mode proposal cannot be confirmed interactively. Commit it
                # with a notice rather than discarding the authored domain. An
                # interactive front-end instead surfaces ``proposed_domain`` and
                # calls ``mgr.commit_domain`` after the operator confirms.
                if (
                    getattr(division, "pending_confirmation", False)
                    and getattr(division, "proposed_domain", None) is not None
                ):
                    if not quiet:
                        print(
                            "[manager] ARGUS_SKILL_DOMAIN_ASK set but no interactive "
                            f"turn here — committing proposed domain `{division.vertical}`",
                            file=sys.stderr,
                        )
                    division = mgr.commit_domain(
                        division.task,
                        division.proposed_domain,
                        execution_task=division.execution_task,
                        workflow_mode=division.workflow_mode,
                    )
                from ..manager.front_door import require_manager_execution_task

                continuous_objective = require_manager_execution_task(division)
                if not quiet:
                    print(division.headline(), file=sys.stderr)
            except Exception as exc:  # noqa: BLE001 — fail closed, preserve bounded work
                log.error(
                    "Manager handoff failed; continuous objective not dispatched: %s",
                    exc,
                )
                continuous = False
                continuous_objective = ""
        cfg = _build_supervisor_config(
            per_mission_cap_usd=per_mission_cap_usd,
            daily_cap_usd=daily_cap_usd,
            global_daily_cap_usd=global_daily_cap_usd,
            once=once,
            max_missions=max_missions,
            project_worktree=project_worktree,
            stop_event=stop_event,
            project_root=project_root,
            artifact_root=artifact_root,
            runtime_context=runtime_context,
            continuous=continuous,
            continuous_objective=continuous_objective,
            open_ended=open_ended,
        )
        sup = LifeSupervisor(
            memory=mem,
            runner=runner,
            sink=sink,
            config=cfg,
            engineer_model=engineer_model,
            reviewer_model=reviewer_model,
            planner_runner=getattr(runner, "planner_backend", None)
            or getattr(runner, "backend", None),
        )
        return sup.run()
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)


def _invoke_supervisor(
    *,
    mem: _SplitMemory,
    backend: str,
    once: bool,
    max_missions: int,
    per_mission_cap_usd: float,
    daily_cap_usd: float,
    global_daily_cap_usd: float,
    quiet: bool = False,
    seed_thread_id: str | None = None,
    continuous: bool = False,
    continuous_objective: str = "",
    open_ended: bool = True,
    allow_chat_fast_path: bool = False,
) -> tuple[dict[str, Any], str | None]:
    ns = argparse.Namespace()
    ns.backend = backend
    ns.engineer_model = resolve_role_model(
        "engineer",
        role_env="ARGUS_SKILL_ENGINEER_MODEL",
    )
    ns.reviewer_model = resolve_role_model(
        "reviewer",
        role_env="ARGUS_SKILL_REVIEWER_MODEL",
    )
    ns.engineer_reasoning_effort = resolve_role_reasoning_effort(
        "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    )
    ns.reviewer_reasoning_effort = resolve_role_reasoning_effort(
        "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
    )
    ns.skills_dir = os.environ.get(
        "ARGUS_SKILL_SKILLS_DIR",
        str(_memory_global_root(mem) / "skills"),
    )
    ns.workdir = os.environ.get("ARGUS_SKILL_WORKDIR")
    os.environ["ARGUS_SKILL_AGENT_IO_LOG"] = str(
        _memory_project_root(mem) / "events.jsonl"
    )
    try:
        ns.manager_session_root = str(_memory_project_root(mem))
        ns.project_state_dir = str(_memory_project_root(mem))
    except Exception:  # noqa: BLE001
        ns.manager_session_root = None
        ns.project_state_dir = None
    # Life-mode default: 500 engineer rounds. The earlier low cap was
    # too small for "implement + test + polish" tasks that need many
    # tool calls. Override via ARGUS_SKILL_MAX_ROUNDS.
    ns.max_rounds = int(os.environ.get("ARGUS_SKILL_MAX_ROUNDS", "500"))

    # Runtime context injected into every mission prelude so the agent
    # knows its own backend, models, and budget constraints at runtime.
    runner_backend = os.environ.get("ARGUS_SKILL_RUNNER_BACKEND") or backend
    mode_label = "continuous" if continuous else "single-shot"
    runtime_context = (
        f"## Runtime info\n"
        f"- Life backend: {backend}\n"
        f"- Runner backend: {runner_backend}\n"
        f"- Engineer model: {ns.engineer_model}\n"
        f"- Reviewer model: {ns.reviewer_model}\n"
        f"- Engineer reasoning effort: {ns.engineer_reasoning_effort or '(default)'}\n"
        f"- Reviewer reasoning effort: {ns.reviewer_reasoning_effort or '(default)'}\n"
        f"- Max rounds per mission: {ns.max_rounds}\n"
        f"- Per-mission budget cap: ${per_mission_cap_usd:.2f}\n"
        f"- Daily budget cap: ${daily_cap_usd:.2f}\n"
        f"- Global daily budget cap: ${global_daily_cap_usd:.2f}\n"
        f"- Mode: {mode_label}\n"
        f"- Command workdir: {Path.cwd()}\n"
        f"- Harness artifact root: {_memory_project_root(mem)}\n"
        "- Keep pipeline/checklist/domain/audit artifacts in the harness artifact "
        "root; do not reuse stale `research/` state from the command workdir.\n"
    )
    from ..life.research_profile import render_research_profile_context

    research_context = render_research_profile_context()
    if research_context:
        runtime_context = runtime_context + "\n---\n\n" + research_context

    runner = build_life_runner(ns, seed_thread_id=seed_thread_id)
    # Chat fast-path is operator-front-door-only: only human free text sent to the
    # cockpit is eligible. Planner / backlog / daemon missions keep the
    # runner default (False) so the harness never classifies agent work.
    if hasattr(runner, "_allow_chat_fast_path"):
        runner._allow_chat_fast_path = bool(allow_chat_fast_path)
    summary = run_life_supervisor(
        mem=mem,
        runner=runner,
        engineer_model=ns.engineer_model,
        reviewer_model=ns.reviewer_model,
        once=once,
        max_missions=max_missions,
        per_mission_cap_usd=per_mission_cap_usd,
        daily_cap_usd=daily_cap_usd,
        global_daily_cap_usd=global_daily_cap_usd,
        project_worktree=getattr(mem, "project_worktree", None) or Path.cwd(),
        artifact_root=_memory_project_root(mem),
        quiet=quiet,
        runtime_context=runtime_context,
        continuous=continuous,
        continuous_objective=continuous_objective,
        open_ended=open_ended,
    )
    final_thread_id = getattr(runner, "last_thread_id", None)
    return summary, final_thread_id


__all__ = [
    "_env_flag",
    "_env_int",
    "_self_retryable_transport_failure",
    "_CommonMemory",
    "_SplitMemory",
    "_memory_project_root",
    "_memory_global_root",
    "_resolve_global_root",
    "_checkpoint_path_for",
    "LifeStderrSink",
    "_Outcome",
    "_MemoryRunner",
    "_ScriptedPlannerBackend",
    "_SkillLoopRunner",
    "log",
    "_TEST_DAEMON_PLANNER_SCRIPT_ENV",
    "_format_daemon_mode_cell",
    "_codex_preflight_warning",
    "_inbox_drainer_for",
    "build_life_runner",
    "_build_supervisor_config",
    "run_life_supervisor",
    "_invoke_supervisor",
]
