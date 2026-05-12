"""Unified ``argus-skill`` REPL + runner adapters.

This module owns everything the lifetime-agent interactive loop needs:

- ``run_life_chat_loop``       — public entry point invoked from
                                  ``apps.cli.main`` when the user types
                                  ``argus-skill`` with no subcommand.
                                  The single interactive surface.
- ``run_life_supervisor``      — non-interactive driver kept for
                                  programmatic use (drain a backlog
                                  without a TTY).
- ``LifeStderrSink``           — chat-style event renderer + verbose/
                                  quiet filter (shared with
                                  telegram.notifier).
- ``build_life_runner``        — factory for memory / codex backends.

History: the original layout had a separate ``argus-skill chat
--life`` subcommand. As of Phase 5 (2026-05-08) the bare
``argus-skill`` command IS this REPL, and the chat / go / mission /
life / daemon / up subcommands have been deleted. One REPL, one
renderer, one help screen.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, ClassVar, Protocol

from ..core.ports import EventSink
from ..life import BacklogItem, JournalEntry, LifeMemory, MemoryBundle
from ..life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)

log = logging.getLogger(__name__)


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

    def render_prelude(self, *, objective: str) -> str: ...


def _resolve_global_root(args: argparse.Namespace) -> Path:
    from ..core import paths as core_paths

    life_dir_arg = getattr(args, "life_dir", None)
    if life_dir_arg:
        return Path(life_dir_arg).expanduser()
    return core_paths.global_root()

# ---------------------------------------------------------------------------
# Sink (event rendering)
# ---------------------------------------------------------------------------

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
    # act on and that just clutter the chat scroll (matcher/scientist
    # banter, internal "distill done" weight reports).
    _SILENCED_IN_LIFE: ClassVar[frozenset[str]] = frozenset({
        "loop.start",
        "loop.done",
        "match.info",         # "skill store empty - will distill a new playbook"
        "scientist.start",    # "no high-fit skill — distilling"
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


# ---------------------------------------------------------------------------
# Runner adapters
# ---------------------------------------------------------------------------

@dataclass
class _Outcome:
    """Duck-typed outcome the supervisor reads via ``getattr``."""
    success: bool
    status: str
    stop_reason: str = ""
    rounds: int = 1
    matched_skill_name: str | None = None
    skill_distilled: bool = False
    had_follow_up: bool = False
    last_thread_id: str | None = None
    # Chat fast-path: when True, the supervisor skips iteration / critic
    # because the operator's input was a conversational message (greeting,
    # capability question, ack) that doesn't warrant a polish cycle.
    chat_mode: bool = False
    # Set when the codex backend reports auth-related stderr (expired
    # token, missing API key, etc.). The supervisor uses this to stop
    # early instead of looping over failing missions.
    auth_failure: bool = False


class _MemoryRunner:
    """Deterministic in-process runner for CI / smoke tests.

    Emits a complete sequence of fully-shaped lifecycle events
    (``loop.started`` → ``round.started`` → ``round.main.completed`` →
    ``round.review.completed`` → ``loop.completed``) so the terminal
    renderer prints ``Round 1`` and ``review ✅ done`` cleanly instead
    of the ``round ?`` placeholders that result from missing
    ``round_index`` / ``status`` fields.
    """

    # The supervisor's iteration loop pulls a RunnerBackend off
    # ``runner.backend`` to drive the Critic. ``None`` here means
    # "no critic possible" — items still go ``done`` after the first
    # cycle. Tests that exercise iteration substitute a real backend.
    backend: Any = None

    def execute(
        self,
        *,
        objective: str,
        sink: EventSink,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
        seed_thread_id: str | None = None,  # noqa: ARG002 — protocol parity
    ) -> _Outcome:
        ack = f"(memory backend) acknowledged objective: {objective[:80]}"
        sink.handle_event({
            "type": "loop.started",
            "objective": objective,
            "max_rounds": 1,
        })
        sink.handle_event({
            "type": "round.started",
            "round_index": 1,
        })
        sink.handle_event({
            "type": "round.main.completed",
            "round_index": 1,
            "input_tokens": 800,
            "output_tokens": 200,
            "last_message": ack,
            "turn_completed": True,
        })
        sink.handle_event({
            "type": "round.review.completed",
            "round_index": 1,
            "status": "done",
            "confidence": 1.0,
            "reason": "memory backend: synthetic acknowledgement",
            "next_action": "",
            "input_tokens": 100,
            "output_tokens": 50,
        })
        sink.handle_event({
            "type": "loop.completed",
            "rounds": 1,
            "success": True,
            "stop_reason": "review_done",
        })
        return _Outcome(success=True, status="success", rounds=1)


class _CodexSkillLoopRunner:
    """Runs each mission through a fresh ``SkillLoop`` (codex backend).

    Bypasses the ``ARGUS_SKILL_BACKEND`` env var: when life mode
    selects ``codex`` that's the user's explicit ask, so we always
    construct a real ``CodexRunnerBackend``. This was a real bug —
    previously the backend silently fell back to memory when the env
    var was unset, while the UI happily printed ``backend: codex``.
    """

    def __init__(self, args: argparse.Namespace, *, seed_thread_id: str | None = None) -> None:
        from ..loop import SkillLoop, SkillLoopConfig

        self._SkillLoop = SkillLoop
        self._SkillLoopConfig = SkillLoopConfig
        try:
            from ..adapters.codex_backend import CodexRunnerBackend
            from ..adapters.stream_progress import make_stream_progress_callback
        except ImportError as exc:  # pragma: no cover — depends on optional install
            raise SystemExit(
                f"Codex backend requested but ArgusBot is unavailable: {exc}.\n"
                "Install it: `pip install 'argus-skill[codex]'`."
            ) from exc
        # Per-call sink swap: backend is built once, but the sink rotates
        # for every execute(). A trampoline callback dispatches to the
        # currently-installed sink so codex's stream-json events become
        # ``engineer.progress`` items in whichever sink owns this call.
        self._current_sink: EventSink | None = None
        # Per-mission ledger of failed tool/command beats. Reset on every
        # execute() so warnings don't bleed across missions.
        self._current_failure_ledger: object | None = None

        def _trampoline(stream: str, line: str) -> None:
            sink = self._current_sink
            if sink is None:
                return
            try:
                make_stream_progress_callback(
                    sink, ledger=self._current_failure_ledger
                )(stream, line)
            except Exception:  # noqa: BLE001 — never let logging crash the runner
                pass

        # Mirror build_codex_backend_from_env's env-var contract here so
        # we can also pass event_callback (the helper doesn't expose it).
        backend_name = os.environ.get("ARGUS_SKILL_RUNNER_BACKEND") or None
        runner_bin = os.environ.get("ARGUS_SKILL_RUNNER_BIN") or None
        raw_extra = os.environ.get("ARGUS_SKILL_RUNNER_EXTRA_ARGS", "").strip()
        extra = shlex.split(raw_extra) if raw_extra else None
        self._backend = CodexRunnerBackend(
            backend=backend_name,
            runner_bin=runner_bin,
            default_extra_args=extra,
            event_callback=_trampoline,
        )
        # Expose the underlying backend so the LifeSupervisor's
        # iteration loop can drive a Critic agent through it without
        # building a second codex process.
        self.backend = self._backend
        self._args = args
        # Session continuity: seed_thread_id is the codex session id from
        # the previous mission in the same REPL session. We propagate it
        # into the *first* engineer round of this mission, then update
        # in-place after each execute() so the chat REPL can recover the
        # latest thread_id and forward it to the next mission.
        self._next_seed_thread_id: str | None = seed_thread_id
        self.last_thread_id: str | None = seed_thread_id

    def stream_to(self, sink: EventSink):
        """Context manager: temporarily route stream lines to *sink*.

        Use this when calling ``backend.run_exec()`` directly (critic /
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

    def execute(
        self,
        *,
        objective: str,
        sink: EventSink,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
        seed_thread_id: str | None = None,
    ) -> _Outcome:
        # Chat fast-path. Conversational input (greetings, capability
        # questions, acks) doesn't need matcher → distill → engineer
        # round-loop → reviewer → critic. A trace before this guard:
        # "hello" cost $0.10 + 72s, ran `pwd && ls && rg --files && sed
        # README.md`, then the reviewer rejected it for "doing unrelated
        # repo inspection". The router below short-circuits to a single
        # codex call with a chat prompt — no skill machinery, no
        # reviewer, no writeback. ~$0.001 + ~3s.
        from ..life.router import is_conversational
        if is_conversational(objective):
            return self._chat_quick_reply(
                objective=objective,
                sink=sink,
                seed_thread_id=seed_thread_id,
            )

        args = self._args
        # 7×24 product: default to dangerous_yolo (no bwrap sandbox).
        # The operator runs the daemon on their own box and explicitly
        # consents to autonomous execution; the sandbox only fights us
        # (`bwrap: Can't create file at /.codex: Permission denied`).
        # Operators can opt back into sandbox via ARGUS_SKILL_SAFE_MODE=1.
        safe_mode = os.environ.get("ARGUS_SKILL_SAFE_MODE", "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        config = self._SkillLoopConfig(
            scientist_model=args.scientist_model,
            engineer_model=args.engineer_model,
            reviewer_model=args.reviewer_model,
            max_rounds=args.max_rounds,
            check_commands=[],
            skill_writeback=True,
            distill_on_miss=True,
            dangerous_yolo=not safe_mode,
            full_auto=safe_mode,
            skip_git_repo_check=True,
        )
        loop = self._SkillLoop(
            skills_dir=Path(args.skills_dir),
            scientist_runner=self._backend,
            engineer_runner=self._backend,
            reviewer_runner=self._backend,
            config=config,
            on_event=sink.handle_event,
        )
        full_task = objective
        if prelude_context:
            full_task = f"{prelude_context}\n---\n## Live objective\n{objective}"
        workdir = (
            Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        )
        # Use the seed for the first execute() of this runner; subsequent
        # execute() calls (LifeSupervisor may run several missions in one
        # supervisor.run()) chain off the previous mission's last thread_id.
        seed = self._next_seed_thread_id if seed_thread_id is None else seed_thread_id
        from ..engineer.failed_tool_ledger import FailedToolLedger
        ledger = FailedToolLedger()
        self._current_sink = sink
        self._current_failure_ledger = ledger
        try:
            outcome = loop.run(
                full_task, workdir=workdir, seed_thread_id=seed,
                failed_tool_ledger=ledger,
                objective_for_skill=objective,
            )
        finally:
            self._current_sink = None
            self._current_failure_ledger = None
        new_tid = getattr(outcome, "last_thread_id", None)
        if new_tid:
            self.last_thread_id = new_tid
            self._next_seed_thread_id = new_tid
        auth_fail = getattr(self._backend, "_auth_failure_detected", False)
        if auth_fail:
            self._backend._auth_failure_detected = False
        return _Outcome(
            success=outcome.successful,
            status=outcome.status,
            stop_reason=outcome.reason or "",
            rounds=outcome.round_count,
            matched_skill_name=outcome.skill_used,
            skill_distilled=outcome.skill_distilled,
            last_thread_id=new_tid,
            auth_failure=auth_fail,
        )

    def _chat_quick_reply(
        self,
        *,
        objective: str,
        sink: EventSink,
        seed_thread_id: str | None = None,
    ) -> _Outcome:
        """One-shot codex call for conversational input.

        Bypasses every component of the mission pipeline (matcher,
        distiller, supervised round-loop, reviewer, skill writeback,
        critic). Emits the minimum event sequence needed by the REPL
        renderer + cost-tracking sink: ``loop.start`` → optional
        streaming ``engineer.progress`` (via the trampoline) →
        ``round.main.completed`` (with token counts so cost is
        accounted for) → ``loop.done``.
        """
        from ..core.models import RunnerOptions
        from ..life.router import build_chat_prompt

        args = self._args
        safe_mode = os.environ.get("ARGUS_SKILL_SAFE_MODE", "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        seed = self._next_seed_thread_id if seed_thread_id is None else seed_thread_id

        sink.handle_event({
            "type": "loop.start",
            "text": f"chat: {objective[:80]}",
            "chat_mode": True,
        })

        prompt = build_chat_prompt(objective=objective)
        workdir = (
            Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        )

        # Wire the trampoline so codex's stream-json events still
        # become ``engineer.progress`` items in the REPL. No ledger:
        # nothing to fail on a chat reply.
        self._current_sink = sink
        self._current_failure_ledger = None
        try:
            result = self._backend.run_exec(
                prompt=prompt,
                options=RunnerOptions(
                    model=args.engineer_model,
                    # ``low`` keeps chat fast and cheap; codex's default
                    # ``medium`` over-thinks short replies.
                    reasoning_effort="low",
                    full_auto=safe_mode,
                    skip_git_repo_check=True,
                    dangerous_yolo=not safe_mode,
                    working_dir=str(workdir),
                ),
                run_label="chat-1",
                resume_thread_id=seed,
            )
        finally:
            self._current_sink = None

        last_msg = (result.last_agent_message or "").strip()
        new_tid = getattr(result, "thread_id", None)
        if new_tid:
            self.last_thread_id = new_tid
            self._next_seed_thread_id = new_tid

        # ``round.main.completed`` is the event the cost-tracking sink
        # listens to for engineer-side tokens. Emitting it here keeps
        # the chat fast-path's USD figure honest.
        sink.handle_event({
            "type": "round.main.completed",
            "round_index": 1,
            "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(result, "output_tokens", 0) or 0),
            "last_message": last_msg,
            "turn_completed": True,
        })

        fatal = getattr(result, "fatal_error", None)
        success = (result.exit_code == 0) and not fatal
        status = "done" if success else "error"
        stop_reason = "" if success else (str(fatal) if fatal else f"exit={result.exit_code}")

        auth_fail = getattr(self._backend, "_auth_failure_detected", False)
        if auth_fail:
            self._backend._auth_failure_detected = False

        sink.handle_event({
            "type": "loop.done",
            "text": f"status={status} rounds=1 (chat)",
        })

        return _Outcome(
            success=success,
            status=status,
            stop_reason=stop_reason,
            rounds=1,
            last_thread_id=new_tid,
            chat_mode=True,
            auth_failure=auth_fail,
        )


def _format_daemon_mode_cell(theme, mem: _SplitMemory) -> str:  # noqa: ANN001
    """Banner ``mode`` cell — life + daemon liveness in one line.

    Shows ``life ⚡ daemon: alive (pid X · up Yh)`` when a 7×24 worker
    is draining the backlog in the background, or
    ``life · in-process · no daemon (start --daemon for 7×24)`` when not.
    """
    try:
        from ..daemon.life_worker import read_daemon_status
        from .cli import _format_short_duration
        status = read_daemon_status(mem.project.root)
    except Exception:  # noqa: BLE001
        return f"{theme.bold('life')}    " + theme.dim("in-process · no daemon")
    if status.alive and status.pid is not None:
        uptime = _format_short_duration(status.uptime_seconds or 0.0)
        body = (
            f"{theme.bold('life')}  "
            + theme.bold_green("⚡ daemon")
            + theme.dim(f": pid {status.pid} · up {uptime}")
        )
        return body
    return (
        f"{theme.bold('life')}    "
        + theme.dim("in-process · ")
        + theme.yellow("no daemon")
        + theme.dim("  (start with `argus-skill --daemon` for 7×24)")
    )


def _codex_preflight_warning() -> str | None:
    """Return a one-line warning if the codex backend cannot run, else None.

    Surfaced on the banner so the user does not discover at mission time
    that ArgusBot or the ``codex`` binary are missing. Best-effort: if
    anything raises we stay quiet — a confusing warning is worse than no
    warning, and the real failure path (``_CodexSkillLoopRunner``) will
    print a precise error when a mission actually starts.
    """
    try:
        from ..adapters.codex_backend import _import_argusbot
    except ImportError:
        return "ArgusBot not installed — `pip install 'argus-skill[codex]'`"
    try:  # noqa: SIM105
        _import_argusbot()
    except Exception:  # noqa: BLE001
        return ("ArgusBot importable but codex_autoloop failed to load — "
                "check the install")
    import shutil
    bin_path = os.environ.get("ARGUS_SKILL_RUNNER_BIN") or shutil.which("codex")
    if not bin_path:
        return ("`codex` binary not found on PATH — set ARGUS_SKILL_RUNNER_BIN")
    return None


def _inbox_drainer_for(life_dir: Path):
    """Return a `user_inbox` callable that drains pending messages from
    ``<life_dir>/inbox.jsonl``.

    The CLI's ``argus-skill --notify "<msg>"`` and the REPL's ``/nudge``
    slash command both append to this file. Each call to the returned
    callable returns one message (or ``None``) and advances a tiny
    offset file so the same line is never replayed twice.
    """
    inbox_path = Path(life_dir) / "inbox.jsonl"
    offset_path = Path(life_dir) / "inbox.offset"

    def _drain_one() -> str | None:
        try:
            if not inbox_path.exists():
                return None
            try:
                offset = int(offset_path.read_text().strip() or "0")
            except (OSError, ValueError):
                offset = 0
            with inbox_path.open("rb") as fh:
                fh.seek(offset)
                raw = fh.readline()
                if not raw:
                    return None
                new_offset = fh.tell()
            try:
                offset_path.write_text(str(new_offset), encoding="utf-8")
            except OSError:
                return None
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None
            text = obj.get("text") if isinstance(obj, dict) else None
            if not isinstance(text, str) or not text.strip():
                return None
            return text.strip()
        except Exception:  # noqa: BLE001
            return None

    return _drain_one


def build_life_runner(args: argparse.Namespace, *, seed_thread_id: str | None = None):
    """Return a ``_MissionRunner``-shaped adapter for the requested backend."""
    if args.backend == "memory":
        return _MemoryRunner()
    if args.backend == "codex":
        return _CodexSkillLoopRunner(args, seed_thread_id=seed_thread_id)
    raise SystemExit(f"unknown backend: {args.backend}")


# ---------------------------------------------------------------------------
# Supervisor driver (used by both `life run` and chat-mode free text)
# ---------------------------------------------------------------------------

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
    quiet: bool = False,
    runtime_context: str = "",
    continuous: bool = False,
    continuous_objective: str = "",
) -> dict[str, Any]:
    """Run ``LifeSupervisor`` with proper signal-handler save/restore.

    Restoring previous SIGINT/SIGTERM handlers on exit means the chat
    REPL keeps its Ctrl-C semantics after a /run finishes.
    """
    stop_event = threading.Event()

    def _on_signal(signum: int, frame: Any) -> None:  # noqa: ANN401
        print(f"\nlife: received signal {signum}, requesting stop", file=sys.stderr)
        stop_event.set()

    prev_int = signal.getsignal(signal.SIGINT)
    prev_term = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        from ..life.event_log import JsonlEventSink

        stderr_sink = LifeStderrSink(quiet=quiet)
        sink = JsonlEventSink(stderr_sink, life_dir=mem.project.root)
        cfg = LifeSupervisorConfig(
            budget=LifeBudget(
                per_mission_cap_usd=per_mission_cap_usd,
                daily_cap_usd=daily_cap_usd,
                max_missions=1 if once else max_missions,
            ),
            poll_interval_seconds=2.0,
            stop_event=stop_event,
            user_inbox=_inbox_drainer_for(mem.project.root),
            runtime_context=runtime_context,
            continuous=continuous,
            continuous_objective=continuous_objective,
        )
        sup = LifeSupervisor(
            memory=mem,
            runner=runner,
            sink=sink,
            config=cfg,
            engineer_model=engineer_model,
            reviewer_model=reviewer_model,
            critic_runner=getattr(runner, "backend", None),
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
    quiet: bool = False,
    seed_thread_id: str | None = None,
    continuous: bool = False,
    continuous_objective: str = "",
) -> tuple[dict[str, Any], str | None]:
    ns = argparse.Namespace()
    ns.backend = backend
    ns.engineer_model = os.environ.get("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini")
    ns.reviewer_model = os.environ.get("ARGUS_SKILL_REVIEWER_MODEL", "gpt-5.4")
    ns.scientist_model = os.environ.get("ARGUS_SKILL_SCIENTIST_MODEL", "gpt-5.4")
    ns.skills_dir = os.environ.get(
        "ARGUS_SKILL_SKILLS_DIR",
        str(mem.global_root / "skills"),
    )
    ns.workdir = os.environ.get("ARGUS_SKILL_WORKDIR")
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
        f"- Max rounds per mission: {ns.max_rounds}\n"
        f"- Per-mission budget cap: ${per_mission_cap_usd:.2f}\n"
        f"- Daily budget cap: ${daily_cap_usd:.2f}\n"
        f"- Mode: {mode_label}\n"
    )

    runner = build_life_runner(ns, seed_thread_id=seed_thread_id)
    summary = run_life_supervisor(
        mem=mem,
        runner=runner,
        engineer_model=ns.engineer_model,
        reviewer_model=ns.reviewer_model,
        once=once,
        max_missions=max_missions,
        per_mission_cap_usd=per_mission_cap_usd,
        daily_cap_usd=daily_cap_usd,
        quiet=quiet,
        runtime_context=runtime_context,
        continuous=continuous,
        continuous_objective=continuous_objective,
    )
    final_thread_id = getattr(runner, "last_thread_id", None)
    return summary, final_thread_id


# ---------------------------------------------------------------------------
# Slash-command helpers (in-process; mirror the public CLI subcommands)
# ---------------------------------------------------------------------------

def _parse_add_flags(
    text: str,
    *,
    default_iterate: bool = True,
    default_cycles: int = 6,
    default_budget: float = 30.0,
) -> tuple[bool, int, float, str]:
    """Strip ``--once`` / ``--cycles=N`` / ``--budget=$X`` from an /add body.

    Returns ``(iterate, max_cycles, budget_usd, remaining_text)``. Defaults
    come from the caller (typically session config); the hardcoded fallbacks
    match the long-run positioning: iterate=True with 6 cycles and $30
    budget so the lifetime agent can actually finish a sizeable polish pass.
    """
    iterate = default_iterate
    max_cycles = default_cycles
    budget = default_budget
    tokens = text.split()
    keep: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low == "--once":
            iterate = False
            continue
        if low.startswith("--cycles="):
            try:
                max_cycles = max(1, int(low.split("=", 1)[1]))
            except ValueError:
                pass
            continue
        if low.startswith("--budget="):
            raw = low.split("=", 1)[1].lstrip("$")
            try:
                budget = max(0.0, float(raw))
            except ValueError:
                pass
            continue
        keep.append(tok)
    return iterate, max_cycles, budget, " ".join(keep).strip()


def _add_only(
    mem: _CommonMemory,
    text: str,
    *,
    priority: int = 100,
    iterate: bool = True,
    iteration_max_cycles: int = 6,
    iteration_budget_usd: float = 30.0,
) -> BacklogItem:
    text = text.strip()
    title = text.splitlines()[0][:60].strip() or "(untitled)"
    item = mem.backlog.add(BacklogItem.new(
        title=title,
        objective=text,
        priority=priority,
        max_cost_usd=30.0,
        tags=[],
        iterate=iterate,
        iteration_max_cycles=iteration_max_cycles,
        iteration_budget_usd=iteration_budget_usd,
    ))
    iter_blurb = (
        f", iter≤{item.iteration_max_cycles} ${item.iteration_budget_usd:.1f}"
        if item.iterate else ", once"
    )
    print(
        f"added {item.id}: {item.title}  "
        f"(priority={item.priority}, max_cost=${item.max_cost_usd:.2f}{iter_blurb})",
        flush=True,
    )
    return item


def _backend_cmd(tokens: list[str], chat_state: dict[str, Any]) -> None:
    from ..daemon.life_worker import ContinuousConfigState

    if not tokens:
        print(f"backend: {chat_state['backend']}  (memory or codex)")
        return
    new = tokens[0].lower()
    if new in {"codex", "memory"}:
        state = chat_state.get("continuous_state")
        if isinstance(state, ContinuousConfigState):
            continuous = state.enabled
            objective = state.objective if state.enabled else ""
        else:
            continuous = bool(chat_state.get("config", {}).get("continuous", False))
            objective = str(chat_state.get("continuous_objective", "") or "")
        error = _continuous_session_error(new, continuous, objective)
        if error:
            print(error)
            return
        chat_state["backend"] = new
        print(f"backend: {new}")
        return
    print(
        f"backend {new!r} is not available. Use `codex` or `memory`."
    )


def _continuous_session_error(
    backend: str,
    continuous: bool,
    objective: str,
) -> str:
    from ..daemon.life_worker import continuous_mode_error
    error = continuous_mode_error(backend, continuous, objective)
    if error:
        return f"argus-skill: {error}"
    return ""


_CONFIG_DEFAULTS: dict[str, Any] = {
    "iterate": True,
    "cycles": 6,
    "budget": 30.0,
    "per_mission_cap": 30.0,
    "daily_cap": 180.0,
    "continuous": False,
}

_CONFIG_TYPES: dict[str, type] = {
    "iterate": bool,
    "cycles": int,
    "budget": float,
    "per_mission_cap": float,
    "daily_cap": float,
    "continuous": bool,
}


def _config_cmd(tokens: list[str], chat_state: dict[str, Any],
                life_dir: Path | None = None) -> None:
    """``/config [key=value ...]`` — view or change REPL-session defaults.

    These defaults apply to free-text input and ``/add``/``/run`` when
    the corresponding flag is not explicitly provided. The ``continuous``
    key is also persisted to disk so the background daemon picks it up.
    """
    cfg = chat_state.setdefault("config", dict(_CONFIG_DEFAULTS))
    if not tokens:
        print("session config (continuous syncs to daemon, others are REPL-local):")
        for k, v in cfg.items():
            if isinstance(v, float):
                print(f"  {k:20s} = ${v:.2f}" if k != "iterate" else f"  {k:20s} = {v}")
            elif isinstance(v, bool):
                print(f"  {k:20s} = {'on' if v else 'off'}")
            else:
                print(f"  {k:20s} = {v}")
        print("\n  usage: /config cycles=10 budget=50 daily_cap=300")
        return
    sync_continuous = False
    for tok in tokens:
        if "=" not in tok:
            print(f"  skip: {tok!r} — expected key=value")
            continue
        key, _, val = tok.partition("=")
        key = key.strip().lower().replace("-", "_")
        if key not in _CONFIG_DEFAULTS:
            print(f"  unknown key: {key!r}  "
                  f"(valid: {', '.join(sorted(_CONFIG_DEFAULTS))})")
            continue
        expected = _CONFIG_TYPES[key]
        try:
            val = val.strip().lstrip("$")
            if expected is bool:
                parsed: Any = val.lower() in {"true", "on", "yes", "1"}
            elif expected is int:
                parsed = max(1, int(val))
            else:
                parsed = max(0.0, float(val))
        except ValueError:
            print(f"  bad value for {key}: {val!r}")
            continue
        if key == "continuous" and parsed:
            backend = str(chat_state.get("backend", "") or "codex")
            current_objective = str(
                chat_state.get("continuous_objective", "") or ""
            )
            error = _continuous_session_error(backend, True, current_objective)
            if error:
                print(error)
                continue
        cfg[key] = parsed
        if key == "continuous":
            sync_continuous = True
        if isinstance(parsed, float):
            print(f"  {key} = ${parsed:.2f}")
        elif isinstance(parsed, bool):
            print(f"  {key} = {'on' if parsed else 'off'}")
        else:
            print(f"  {key} = {parsed}")
    # Persist continuous config to disk so daemon can hot-reload.
    if life_dir is not None and sync_continuous:
        from ..daemon.life_worker import write_continuous_config
        write_continuous_config(
            life_dir,
            enabled=cfg.get("continuous", False),
            objective=chat_state.get("continuous_objective", ""),
        )
        print("  (synced to daemon — takes effect within seconds)")


def _identity_cmd(mem: _CommonMemory, tokens: list[str], rest_text: str) -> None:
    if not tokens:
        text = mem.identity.read().strip()
        if not text:
            print("(identity empty — try /identity edit)")
        else:
            print(text)
        return
    sub = tokens[0].lower()
    if sub == "edit":
        print("Enter new identity card. End with a single '.' on its own line:")
        lines: list[str] = []
        while True:
            try:
                ln = input("> ")
            except (EOFError, KeyboardInterrupt):
                print("\n(aborted, identity unchanged)")
                return
            if ln.strip() == ".":
                break
            lines.append(ln)
        new_text = "\n".join(lines).strip() + "\n"
        mem.identity.path.write_text(new_text, encoding="utf-8")
        print(f"identity card updated ({len(lines)} lines)")
        return
    if sub == "set":
        body = rest_text[len("set"):].lstrip() if rest_text.lower().startswith("set") else ""
        if not body:
            print("usage: /identity set <text>")
            return
        mem.identity.path.write_text(body.rstrip() + "\n", encoding="utf-8")
        print("identity card updated")
        return
    print(f"unknown /identity subcommand: {sub}")


def _backlog_list_cmd(mem: _CommonMemory, *, include_all: bool) -> None:
    items = mem.backlog.all() if include_all else [
        i for i in mem.backlog.all() if i.status == "pending"
    ]
    if not items:
        print("(backlog is empty)")
        return
    for it in items:
        print(
            f"  {it.status:<8}  {it.id}  "
            f"p={it.priority:<4}  cap=${it.max_cost_usd:.2f}  "
            f"{it.title}"
        )


def _status_change_cmd(mem: _CommonMemory, cmd: str, item_id: str) -> None:
    if cmd == "/done":
        ok = mem.backlog.mark_done(item_id) is not None
    elif cmd == "/skip":
        ok = mem.backlog.update(item_id, status="skipped") is not None
    else:  # /rm
        ok = mem.backlog.remove(item_id)
    print(f"{cmd[1:]}: {item_id}  {'ok' if ok else '(not found)'}")


def _journal_tail_cmd(mem: _CommonMemory, n: int) -> None:
    entries = mem.journal.tail(n)
    if not entries:
        print("(journal is empty)")
        return
    for e in entries:
        ts = datetime.fromtimestamp(e.ts).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  [{ts}] {e.kind:<14} {e.title}")
        if e.summary:
            print(f"      {e.summary}")


def _free_text_cmd(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
) -> None:
    """Free-text input: enqueue at the head + run immediately on the current backend.

    Free-text typed at the prompt expresses intent ``run THIS now``, so we
    inject the new item with a priority that beats anything ``/add`` can
    produce, and then ask the supervisor to drain a single mission. This
    avoids the surprise of typing "hello" and watching an unrelated
    older backlog item run instead.

    Supports ``--once`` / ``--cycles=N`` / ``--budget=$X`` inline flags
    (same as ``/add``). When no flags are present, session-wide defaults
    from ``chat_state["config"]`` are used.

    When ``config["continuous"]`` is True, the supervisor runs in
    continuous improvement mode: the critic-as-planner inspects the
    project after each completed task and generates new work until
    the project satisfies the objective.
    """
    cfg = chat_state.get("config", {})
    continuous = cfg.get("continuous", False)
    iterate, max_cycles, budget, body = _parse_add_flags(
        text,
        default_iterate=cfg.get("iterate", True),
        default_cycles=cfg.get("cycles", 6),
        default_budget=cfg.get("budget", 30.0),
    )
    body = body or text.strip()
    pending = mem.backlog.pending()
    head_priority = min((it.priority for it in pending), default=100)
    free_priority = min(head_priority - 1, -1)
    _add_only(
        mem,
        body,
        priority=free_priority,
        iterate=iterate,
        iteration_max_cycles=max_cycles,
        iteration_budget_usd=budget,
    )
    theme = chat_state.get("theme")
    if continuous:
        msg = (
            f"🔄 continuous mode on backend={chat_state['backend']} "
            f"(Ctrl-C to stop)..."
        )
        # Persist objective to disk so daemon can pick it up.
        chat_state["continuous_objective"] = body
        from ..daemon.life_worker import write_continuous_config
        write_continuous_config(
            mem.project.root,
            enabled=True,
            objective=body,
        )
    else:
        msg = f"running on backend={chat_state['backend']} (Ctrl-C to stop)..."
    print(theme.gray(msg) if theme else msg, flush=True)
    _invoke_and_track(
        mem=mem,
        chat_state=chat_state,
        once=not continuous,
        max_missions=999 if continuous else 1,
        per_mission_cap_usd=float(os.environ.get(
            "ARGUS_SKILL_PER_MISSION_CAP_USD",
            str(cfg.get("per_mission_cap", 30.0)),
        )),
        daily_cap_usd=float(os.environ.get(
            "ARGUS_SKILL_DAILY_CAP_USD",
            str(cfg.get("daily_cap", 180.0)),
        )),
        quiet=False,
        continuous=continuous,
        continuous_objective=body if continuous else "",
    )


def _format_elapsed(seconds: float) -> str:
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins, secs = divmod(int(seconds), 60)
    if mins < 60:
        return f"{mins}m{secs:02d}s"
    hours, mins = divmod(mins, 60)
    return f"{hours}h{mins:02d}m{secs:02d}s"


def _invoke_and_track(
    *,
    mem: _SplitMemory,
    chat_state: dict[str, Any],
    once: bool,
    max_missions: int,
    per_mission_cap_usd: float,
    daily_cap_usd: float,
    quiet: bool,
    continuous: bool = False,
    continuous_objective: str = "",
) -> dict[str, Any]:
    """Run the supervisor and persist the resulting codex thread_id back
    into ``chat_state`` so the next mission resumes the same session.

    Also records wall-clock elapsed time and prints a one-line footer
    so the user sees how long each mission took.
    """
    seed = chat_state.get("last_thread_id")
    theme = chat_state.get("theme")
    if seed and not quiet:
        note = f"resuming codex session {seed[:12]}…"
        print(theme.gray(note) if theme else note)
    t0 = time.monotonic()
    summary, last_tid = _invoke_supervisor(
        mem=mem,
        backend=chat_state["backend"],
        once=once,
        max_missions=max_missions,
        per_mission_cap_usd=per_mission_cap_usd,
        daily_cap_usd=daily_cap_usd,
        quiet=quiet,
        seed_thread_id=seed,
        continuous=continuous,
        continuous_objective=continuous_objective,
    )
    elapsed = time.monotonic() - t0
    if last_tid:
        chat_state["last_thread_id"] = last_tid
    chat_state["last_elapsed_s"] = elapsed
    chat_state["total_elapsed_s"] = (
        chat_state.get("total_elapsed_s", 0.0) + elapsed
    )
    chat_state["mission_count"] = chat_state.get("mission_count", 0) + 1
    if not quiet:
        ran = int(summary.get("missions_run", 0)) if isinstance(summary, dict) else 0
        cost = float(summary.get("total_cost_usd", 0.0)) if isinstance(summary, dict) else 0.0
        footer = (
            f"⏱  elapsed {_format_elapsed(elapsed)}"
            + (f"  ·  missions={ran}" if ran else "")
            + (f"  ·  cost=${cost:.4f}" if cost else "")
        )
        print(theme.dim(footer) if theme else footer)

    # Surface auth failures prominently so the user knows to re-login
    # (the supervisor already set the stop event, but the REPL user
    # may not read stderr logs).
    if isinstance(summary, dict) and summary.get("stopped_by") == "auth_failure":
        warn = (
            "⚠  codex authentication failed — run `codex login` to "
            "refresh credentials, then restart the REPL or daemon."
        )
        print(theme.yellow(warn) if theme and hasattr(theme, "yellow") else warn)

    return summary


def _run_cmd(
    mem: _SplitMemory,
    opts: list[str],
    chat_state: dict[str, Any],
) -> None:
    cfg = chat_state.get("config", {})
    p = argparse.ArgumentParser(prog="/run", add_help=False)
    p.add_argument("--once", action="store_true")
    p.add_argument(
        "--backend",
        choices=("codex",),
        default=chat_state["backend"],
    )
    p.add_argument("--max-missions", type=int,
                   default=int(cfg.get("cycles", 6)))
    p.add_argument("--per-mission-cap-usd", type=float,
                   default=float(cfg.get("per_mission_cap", 30.0)))
    p.add_argument("--daily-cap-usd", type=float,
                   default=float(cfg.get("daily_cap", 180.0)))
    p.add_argument("--quiet", action="store_true")
    try:
        run_args = p.parse_args(opts)
    except SystemExit:
        return

    print(
        f"/run: backend={run_args.backend}  "
        f"max_missions={'1 (once)' if run_args.once else run_args.max_missions}  "
        f"per_mission_cap=${run_args.per_mission_cap_usd:.2f}  "
        f"daily_cap=${run_args.daily_cap_usd:.2f}",
        flush=True,
    )
    print("       (foreground; Ctrl-C requests graceful stop)", flush=True)

    # If user overrode backend on /run, we still resume only when it matches
    # the chat_state backend (where the thread was originally created).
    use_seed = run_args.backend == chat_state["backend"]
    seed = chat_state.get("last_thread_id") if use_seed else None
    theme = chat_state.get("theme")
    if seed and not run_args.quiet:
        note = f"resuming codex session {seed[:12]}…"
        print(theme.gray(note) if theme else note)
    t0 = time.monotonic()
    summary, last_tid = _invoke_supervisor(
        mem=mem,
        backend=run_args.backend,
        once=run_args.once,
        max_missions=run_args.max_missions,
        per_mission_cap_usd=run_args.per_mission_cap_usd,
        daily_cap_usd=run_args.daily_cap_usd,
        quiet=run_args.quiet,
        seed_thread_id=seed,
    )
    elapsed = time.monotonic() - t0
    if last_tid and use_seed:
        chat_state["last_thread_id"] = last_tid
    chat_state["last_elapsed_s"] = elapsed
    chat_state["total_elapsed_s"] = chat_state.get("total_elapsed_s", 0.0) + elapsed
    if isinstance(summary, dict):
        summary.setdefault("elapsed_s", round(elapsed, 3))
    print("\n--- /run summary ---")
    print(json.dumps(summary, indent=2, default=str))
    footer = f"⏱  /run elapsed {_format_elapsed(elapsed)}"
    print(theme.dim(footer) if theme else footer)


def _status_cmd(mem: _SplitMemory, chat_state: dict[str, Any] | None = None) -> None:
    """Lightweight status print (mirrors `argus-skill life status` output)."""
    from ..daemon.life_worker import ContinuousConfigState, read_continuous_state

    identity = mem.identity.read().strip()
    if identity:
        first = identity.splitlines()[0][:80]
        print(f"identity: {first}{'…' if len(identity) > 80 else ''}")
    else:
        print("identity: (empty)")
    pending = mem.backlog.pending()
    print(f"backlog : {len(pending)} pending  "
          f"({len(mem.backlog.all())} total)")
    for it in pending[:5]:
        print(f"  - {it.id} (p={it.priority}): {it.title}")
    if len(pending) > 5:
        print(f"  … {len(pending) - 5} more")
    last = mem.journal.tail(3)
    if last:
        print("recent journal:")
        for e in last:
            ts_str = datetime.fromtimestamp(e.ts).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  [{ts_str}] {e.kind} — {e.title}")
    cont = None
    if chat_state is not None:
        cont = chat_state.get("continuous_state")
    if not isinstance(cont, ContinuousConfigState):
        cont = read_continuous_state(mem.project.root)
    print(f"continuous: {'on' if cont.enabled else 'off'}")
    if cont.objective:
        print(f"  objective: {cont.objective}")
    if cont.done_reason:
        print(f"  done_reason: {cont.done_reason}")
    if cont.done_at:
        print(f"  done_at: {cont.done_at}")
    if chat_state is not None:
        started = chat_state.get("session_started_s")
        if started is not None:
            uptime = time.monotonic() - started
            count = int(chat_state.get("mission_count", 0))
            total = float(chat_state.get("total_elapsed_s", 0.0))
            last_e = chat_state.get("last_elapsed_s")
            line = f"timing : uptime {_format_elapsed(uptime)}"
            if count:
                line += (
                    f"  ·  {count} mission{'s' if count != 1 else ''}"
                    f" totaling {_format_elapsed(total)}"
                )
            if last_e is not None:
                line += f"  ·  last {_format_elapsed(last_e)}"
            print(line)
    # Background daemon status — surfaces the 7×24 worker so /status
    # answers "is anything running while I'm idle?".
    try:
        from ..daemon.life_worker import read_daemon_status
        from .cli import _format_short_duration
        ds = read_daemon_status(mem.project.root)
    except Exception:  # noqa: BLE001
        ds = None
    if ds is not None:
        if ds.alive and ds.pid is not None:
            up = _format_short_duration(ds.uptime_seconds or 0.0)
            print(f"daemon : alive (pid {ds.pid}, up {up}, "
                  f"backend {ds.backend or '?'})")
        else:
            print("daemon : not running   (start with `argus-skill --daemon`)")
            tid = chat_state.get("last_thread_id") if chat_state is not None else None
            if tid:
                print(f"codex  : resuming session {tid[:12]}…  (/reset to drop)")


# ---------------------------------------------------------------------------
# Help screen
# ---------------------------------------------------------------------------

def _render_help(theme) -> str:  # noqa: ANN001
    rows: list[tuple[str, str]] = [
        ("/help", "show this help"),
        ("/status", "summary of identity, backlog, recent journal"),
        ("/config [key=val ...]", "view/change session defaults "
                                  "(cycles, budget, continuous, daily_cap)"),
        ("/identity [edit|set …]", "view or update the identity card"),
        ("/backlog [all]", "list pending (or all) items"),
        ("/add <text> [--once] [--cycles=N] [--budget=$X]",
            "enqueue a mission (iterates by default until critic stops)"),
        ("/done|/skip|/rm <id>", "change item status"),
        ("/stop <id>", "disable iteration on an item (let it finish naturally)"),
        ("/journal [N]", "tail last N journal entries (default 10)"),
        ("/note <text>", "append a manual journal note"),
        ("/nudge <text>", "send live operator guidance — the next "
                          "engineer round will see it"),
        ("/run [opts]", "drain the backlog (foreground; Ctrl-C stops)"),
        ("/skills [ls|promote <name>]",
            "list global skills or promote a project skill to global"),
        ("/reset", "drop codex session — next mission starts fresh"),
        ("/backend", "show or change the backend (codex / memory)"),
        ("/exit  /quit  :q", "leave the REPL (Ctrl-D also works)"),
    ]
    width = max(len(k) for k, _ in rows)
    out: list[str] = []
    out.append(theme.bold("argus-skill")
               + theme.gray("  — unified lifetime-agent REPL"))
    out.append("")
    out.append(theme.gray("Slash commands:"))
    for key, desc in rows:
        out.append(f"  {theme.cyan(key.ljust(width))}  {theme.gray(desc)}")
    out.append("")
    out.append(theme.gray(
        "Free text (no leading '/') is appended to the backlog AND runs immediately."
    ))
    out.append(theme.gray(
        "Supports --once / --cycles=N / --budget=$X inline flags."
    ))
    out.append(theme.gray(
        "Use /config continuous=true to enable 24/7 continuous improvement mode."
    ))
    out.append(theme.gray(
        "In continuous mode the critic-as-planner inspects the project after each "
        "task and generates new work until the objective is fully satisfied."
    ))
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Slash-command helpers + public REPL entry point — invoked by apps/cli.main
# ---------------------------------------------------------------------------

def _skills_cmd(mem: _CommonMemory, tokens: list[str]) -> None:
    """``/skills [ls|promote <name>]`` — inspect or promote a skill
    from the current project layer to the global layer."""
    op = (tokens[0].lower() if tokens else "ls")
    if op in ("ls", "list"):
        from ..core import paths as core_paths
        from ..skills.store import SkillStore

        global_store = SkillStore(core_paths.skills_global_root())
        rows = global_store.list_summaries()
        if not rows:
            print("(no global skills)")
            return
        for s in rows:
            print(f"- {s['name']}  ({s.get('category') or '-'})  "
                  f"{s['description']}")
        return
    if op == "promote":
        if len(tokens) < 2:
            print("usage: /skills promote <name>")
            return
        name = tokens[1]
        from ..core import paths as core_paths
        from ..core.project import project_fingerprint
        from ..skills.layered import LayeredSkillStore

        try:
            fp = project_fingerprint(Path.cwd()).fingerprint
        except Exception as exc:  # noqa: BLE001
            print(f"could not compute project fingerprint: {exc}")
            return
        layered = LayeredSkillStore(
            project_dir=core_paths.project_skills_root(fp),
            global_dir=core_paths.skills_global_root(),
        )
        # Find the project skill by name.
        target = None
        for s in layered.project.list_summaries():
            if s["name"].casefold() == name.casefold():
                target = layered.project.load(s["path"])
                break
        if target is None:
            print(f"no project skill named {name!r} to promote")
            return
        try:
            promoted = layered.promote_to_global(target)
        except Exception as exc:  # noqa: BLE001
            print(f"promote failed: {exc}")
            return
        print(f"promoted {promoted.name} → global ({promoted.path})")
        return
    print(f"unknown /skills subcommand: {op}  (try ls | promote)")


def _seed_chat_state(
    args: argparse.Namespace,
    mem: LifeMemory | MemoryBundle,
    *,
    theme: Any,
) -> tuple[dict[str, Any], str | None]:
    from ..daemon.life_worker import ContinuousConfigState, read_continuous_state

    project_root = getattr(mem, "project_root", None)
    if project_root is None:
        project = getattr(mem, "project", None)
        project_root = getattr(project, "root", None)
    if project_root is None:
        project_root = getattr(mem, "root")

    backend_default = getattr(args, "backend", None) or os.environ.get(
        "ARGUS_SKILL_LIFE_BACKEND",
        "codex",
    )
    disk_state = read_continuous_state(Path(project_root))
    cli_continuous = bool(getattr(args, "continuous", False))
    cli_objective = str(getattr(args, "objective", "") or "").strip()
    disk_objective = disk_state.objective.strip()
    continuous = cli_continuous or disk_state.enabled
    objective = cli_objective or (disk_objective if disk_state.enabled else "")
    error = _continuous_session_error(backend_default, continuous, objective)
    if error:
        return {}, error

    chat_state: dict[str, Any] = {
        "backend": backend_default,
        "theme": theme,
        # Codex CLI session id of the most recent mission. Reused as
        # ``resume_thread_id`` on the next mission so the codex CLI does
        # NOT spin up a fresh session for every prompt. Cleared by /reset.
        "last_thread_id": None,
        # Wall-clock timing — populated as missions run so /status and the
        # post-mission footer can report uptime / per-mission elapsed.
        "session_started_s": time.monotonic(),
        "mission_count": 0,
        "total_elapsed_s": 0.0,
        "last_elapsed_s": None,
        # Session-wide iteration/budget defaults. Changed via /config.
        # REPL-local only — does not affect the background daemon.
        "config": dict(_CONFIG_DEFAULTS),
        "continuous_objective": objective or disk_objective,
    }
    chat_state["config"]["continuous"] = continuous
    chat_state["continuous_state"] = ContinuousConfigState(
        enabled=continuous,
        objective=objective or disk_objective,
        done_reason="" if continuous else disk_state.done_reason,
        done_at="" if continuous else disk_state.done_at,
    )
    return chat_state, None


def run_life_chat_loop(args: argparse.Namespace) -> int:
    """Drive the unified ``argus-skill`` REPL.

    Slash commands dispatch in-process — no daemon, no jsonl bus.
    Free text becomes a backlog item AND runs immediately on the
    current default backend.
    """
    global_root = _resolve_global_root(args)
    mem: MemoryBundle = MemoryBundle.for_cwd(Path.cwd(), global_root=global_root)
    state = mem.init()
    created: list[str] = []
    for scope, rows in state.items():
        for name, was_created in rows.items():
            if was_created:
                created.append(f"{scope}.{name}")
    theme = None  # populated in the locked body

    # Fail fast before we take the singleton lock if the requested
    # session cannot ever enter continuous mode.
    chat_state, error = _seed_chat_state(args, mem, theme=theme)
    if error:
        sys.stderr.write(error + "\n")
        return 2

    # Singleton guard: two argus-skill REPLs running against the same
    # life-dir would race on backlog.jsonl rewrites and corrupt journal
    # appends mid-flight. Acquire an OS-level advisory lock per life-dir
    # so a second invocation gets a clear error instead of silent
    # corruption. The lock auto-releases when the process exits; we also
    # release explicitly via try/finally below.
    from ..core.daemon_lock import (
        DaemonAlreadyRunning,
        acquire_global_daemon_lock,
    )
    lock_path = mem.project.root / "repl.pid"
    try:
        repl_lock = acquire_global_daemon_lock(pid_path=lock_path)
    except DaemonAlreadyRunning as exc:
        sys.stderr.write(
            f"argus-skill: another REPL is already running here "
            f"(pid={exc.pid}, lock={exc.lock_path}).\n"
            f"  ↳ if that process is dead, remove {exc.lock_path} and retry.\n"
        )
        return 2

    try:
        return _run_life_chat_loop_locked(args, mem, created, chat_state=chat_state)
    finally:
        try:
            repl_lock.release()
        except Exception:  # noqa: BLE001
            log.exception("life REPL: failed to release singleton lock")


def _run_life_chat_loop_locked(
    args: argparse.Namespace,
    mem: _SplitMemory,
    created: list[str],
    *,
    chat_state: dict[str, Any],
) -> int:
    """The interactive REPL body. Split out so the singleton lock in
    :func:`run_life_chat_loop` cleanly wraps the entire loop with
    a try/finally release."""
    import readline  # noqa: F401 — enables line-editing for input()

    from ._input_helpers import enable_bracketed_paste, read_pasted_message
    enable_bracketed_paste()
    from .. import __version__ as _argus_version
    from ..cli.branding import TAGLINE, render_logo
    from ..cli.theme import Theme

    theme = Theme.auto(force=getattr(args, "color", None))

    # Always-verbose: the lifetime-agent product positioning means the
    # operator wants to see every internal event (round.start, match.info,
    # skill.writeback, …). The earlier ``verbose``/``quiet`` toggles have
    # been removed; ``--verbose`` and ``--quiet`` flags are accepted but
    # ignored (kept for backward compat in scripts).

    backend_default = chat_state["backend"]
    chat_state["theme"] = theme

    # ── Auto-spawn 7×24 daemon ────────────────────────────────────
    # Lifetime-agent positioning means the daemon is the default. We
    # silently spawn one in the background unless the user opted out
    # with --no-daemon or one is already alive (idempotent: spawn is
    # a no-op when the singleton lock is held).
    auto_spawn_msg: str | None = None
    legacy_zombie_msg: str | None = None
    # Detect a pre-pivot ``python -m argus_skill daemon`` zombie still
    # writing to the legacy ``state/`` dir. Two independent daemons will
    # double-claim work and corrupt accounting, so we surface this loudly.
    legacy_status = mem.global_mem.root / "state" / "status.json"
    if legacy_status.exists():
        try:
            data = json.loads(legacy_status.read_text(encoding="utf-8"))
            zpid = int(data.get("daemon_pid") or 0)
            if zpid > 0:
                try:
                    os.kill(zpid, 0)
                    legacy_zombie_msg = (
                        f"legacy daemon detected (pid {zpid}, pre-pivot). "
                        f"Run: kill {zpid} && rm -rf {legacy_status.parent}"
                    )
                except OSError:
                    legacy_zombie_msg = None
        except Exception:  # noqa: BLE001
            pass
    if not getattr(args, "no_daemon", False):
        try:
            from ..daemon.life_worker import (
                read_daemon_status,
                spawn_detached_daemon,
                wait_for_daemon_status,
            )
            from .cli import _build_worker_config
            status = read_daemon_status(mem.project.root)
            if not status.alive:
                cfg = _build_worker_config(args)
                spawn_rc = spawn_detached_daemon(cfg)
                if spawn_rc == 0:
                    started = wait_for_daemon_status(mem.project.root)
                    if started is not None and started.pid is not None:
                        auto_spawn_msg = f"daemon auto-spawned (pid {started.pid})"
                    else:
                        auto_spawn_msg = "daemon auto-spawned"
        except Exception as exc:  # noqa: BLE001
            auto_spawn_msg = f"daemon auto-spawn skipped: {exc!s}"

    # ── Banner ─────────────────────────────────────────────────────
    print()
    print(render_logo(theme=theme))
    print()
    print("  " + theme.italic(theme.gray(TAGLINE))
          + "  " + theme.dim(f"v{_argus_version}"))
    print()
    rule = theme.dim("─" * min(theme.width - 2, 60))
    print("  " + rule)

    arrow = theme.dim("→")
    label = lambda s: theme.gray(f"{s:<10}")  # noqa: E731
    cfg = chat_state["config"]
    iter_status = theme.bold_green("on") if cfg["iterate"] else theme.bold("off")
    iter_detail = (
        f"default {cfg['cycles']} cycles · ${cfg['budget']:.0f} budget"
        f" · /add --once to opt out"
    )
    rows = [
        ("mode",    f"{theme.bold('life')}    " + theme.dim("in-process · no daemon")),
        ("backend", f"{theme.bold(backend_default)}   " + theme.dim("(memory or codex)")),
        ("backlog", f"{theme.bold(str(len(mem.backlog.pending())))} "
                    + theme.gray("pending")),
        ("iterate", iter_status + "    "
                    + theme.dim(iter_detail)),
        ("verbose", theme.bold_green("always") + " "
                    + theme.dim("(filter removed — every event is shown)")),
        ("state",   theme.cyan(str(mem.project.root))),
    ]
    # Replace the static "in-process · no daemon" hint with live daemon
    # status so the user sees whether the 7×24 worker is draining the
    # backlog in the background. The lifetime-agent positioning is only
    # honest if this is observable at a glance.
    rows[0] = ("mode", _format_daemon_mode_cell(theme, mem))
    for k, v in rows:
        print(f"  {label(k)} {arrow} {v}")
    if created:
        print(f"  {label('init')} {arrow} " + theme.dim("created ")
              + theme.cyan(", ".join(created)))
    if auto_spawn_msg:
        print(f"  {label('daemon')} {arrow} " + theme.dim(auto_spawn_msg))
    if legacy_zombie_msg:
        print(f"  {label('warn')} {arrow} " + theme.yellow(legacy_zombie_msg))
    # Preflight: surface codex-backend problems at launch, not mid-mission.
    if backend_default == "codex":
        warning = _codex_preflight_warning()
        if warning:
            print(f"  {label('warn')} {arrow} " + theme.yellow(warning))
    print("  " + rule)
    print()
    print("  " + theme.gray("free text runs immediately on the backend  ·  ")
          + theme.cyan("/help") + theme.gray(" for commands  ·  ")
          + theme.cyan("/exit") + theme.gray(" or Ctrl-D to leave"))
    print()

    base_prompt = theme.bold(theme.cyan("argus"))
    sep = theme.dim(" › ")
    resume_marker = theme.dim(" ↻")  # subtle indicator when codex session is being reused

    while True:
        prompt = (
            base_prompt + (resume_marker if chat_state.get("last_thread_id") else "") + sep
        )
        try:
            raw = read_pasted_message(prompt)
        except KeyboardInterrupt:
            print()
            continue
        if raw is None:
            print()
            print(theme.gray("bye."))
            return 0
        line = raw.strip()
        if not line:
            continue

        if line in ("/quit", "/exit", ":q", ":quit"):
            print(theme.gray("bye."))
            return 0

        if not line.startswith("/"):
            _free_text_cmd(mem, raw, chat_state)
            continue

        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            print(theme.red(f"parse error: {exc}"))
            continue
        cmd = tokens[0].lower()
        rest = tokens[1:]
        rest_text = line[len(tokens[0]):].lstrip()

        if cmd in ("/help", "/commands"):
            sys.stdout.write(_render_help(theme))
            sys.stdout.flush()
            continue
        if cmd == "/status":
            _status_cmd(mem, chat_state)
            continue
        if cmd == "/identity":
            _identity_cmd(mem, rest, rest_text)
            continue
        if cmd == "/backlog":
            include_all = bool(rest) and rest[0].lower() == "all"
            _backlog_list_cmd(mem, include_all=include_all)
            continue
        if cmd == "/add":
            if not rest_text:
                print(theme.gray(
                    "usage: /add <objective>  "
                    "[--once] [--cycles=N] [--budget=$X]"
                ))
                continue
            cfg = chat_state.get("config", {})
            iterate, max_cycles, budget, body = _parse_add_flags(
                rest_text,
                default_iterate=cfg.get("iterate", True),
                default_cycles=cfg.get("cycles", 6),
                default_budget=cfg.get("budget", 30.0),
            )
            if not body:
                print(theme.gray("/add: empty objective after flags"))
                continue
            _add_only(
                mem,
                body,
                iterate=iterate,
                iteration_max_cycles=max_cycles,
                iteration_budget_usd=budget,
            )
            continue
        if cmd == "/stop":
            if not rest:
                print(theme.gray("usage: /stop <item_id>"))
                continue
            stopped = mem.backlog.stop_iteration(rest[0])
            if stopped is None:
                print(theme.red(f"/stop: no item with id {rest[0]!r}"))
            else:
                print(
                    f"iteration disabled for {stopped.id}: {stopped.title}  "
                    f"(status={stopped.status})"
                )
            continue
        if cmd in ("/done", "/skip", "/rm"):
            if not rest:
                print(theme.gray(f"usage: {cmd} <item_id>"))
                continue
            _status_change_cmd(mem, cmd, rest[0])
            continue
        if cmd == "/journal":
            n = 10
            if rest:
                try:
                    n = int(rest[0])
                except ValueError:
                    print(theme.gray(f"usage: /journal [N]  (got: {rest[0]!r})"))
                    continue
            _journal_tail_cmd(mem, n)
            continue
        if cmd == "/note":
            if not rest_text:
                print(theme.gray("usage: /note <text>"))
                continue
            entry = JournalEntry.new(
                kind="user_note",
                title="manual note",
                summary=rest_text,
                tags=[],
            )
            mem.journal.append(entry)
            print(theme.gray(f"note appended (id={entry.id})"))
            continue
        if cmd in ("/nudge", "/inject", "/notify"):
            if not rest_text:
                print(theme.gray("usage: /nudge <message>  (one line, "
                                 "spliced into the next engineer round)"))
                continue
            inbox = mem.project.root / "inbox.jsonl"
            inbox.parent.mkdir(parents=True, exist_ok=True)
            record = {"ts": time.time(), "text": rest_text}
            with inbox.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(theme.gray(
                f"nudge queued ({len(rest_text)} chars) → next mission round "
                f"will see it as operator guidance"
            ))
            continue
        if cmd == "/backend":
            _backend_cmd(rest, chat_state)
            continue
        if cmd == "/config":
            _config_cmd(rest, chat_state, life_dir=mem.project.root)
            continue
        if cmd in ("/verbose", "/quiet"):
            print(theme.gray(
                "verbose is always on now (the toggle was removed). "
                "every event is rendered."
            ))
            continue
        if cmd == "/reset":
            old = chat_state.get("last_thread_id")
            chat_state["last_thread_id"] = None
            if old:
                print(theme.gray(f"reset: dropped codex session {old[:12]}…  "
                                 "next mission will start fresh"))
            else:
                print(theme.gray("reset: no active codex session"))
            continue
        if cmd == "/run":
            _run_cmd(mem, rest, chat_state)
            continue
        if cmd == "/skills":
            _skills_cmd(mem, rest)
            continue
        print(theme.gray(f"unknown command: {cmd}  (try /help)"))


__all__ = [
    "run_life_chat_loop",
    "run_life_supervisor",
    "build_life_runner",
    "LifeStderrSink",
]
