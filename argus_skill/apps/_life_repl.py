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
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, ClassVar, Protocol

from ..core import paths as core_paths
from ..core.models import RunnerResult
from ..core.ports import EventSink
from ..engineer.runner import should_clear_thread_id_after_outcome
from ..life import BacklogItem, LifeMemory, MemoryBundle
from ..life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)
from ._life_actions import (
    _continuous_session_error as _shared_continuous_session_error,
)
from ._life_actions import (
    add_backlog_item,
    append_note,
    format_added_item,
    format_backlog_list,
    format_journal_tail,
    format_status_change,
    parse_add_flags,
    render_backend_cmd,
    render_config_cmd,
    render_identity_cmd,
    render_project_cmd,
    render_reset_cmd,
    render_run_command,
    render_skills_cmd,
    stop_iteration,
)
from ._target_paths import resolve_life_root

log = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


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


def _checkpoint_path_for(args: argparse.Namespace, workdir: Path) -> Path | None:
    """Per-project curated-checkpoint file in the project state dir.

    Lives next to ``events.jsonl`` / ``memory.jsonl`` under
    ``<global_root>/projects/<fingerprint>/checkpoint.json`` so the reviewer's
    per-round handoff survives across missions and daemon restarts, never the
    git work-tree (which the agent might commit). Set
    ``ARGUS_SKILL_CHECKPOINT_PERSIST=0`` to opt back into in-memory-only.
    """
    if not _env_flag("ARGUS_SKILL_CHECKPOINT_PERSIST", True):
        return None
    try:
        from ..core.project import project_fingerprint

        global_root = _resolve_global_root(args)
        fingerprint = project_fingerprint(workdir).fingerprint
        state_dir = global_root / "projects" / fingerprint
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir / "checkpoint.json"
    except Exception:  # noqa: BLE001 — never let path resolution break a mission
        return None

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
    # Reviewer completion contract (replaces the retired EMNLP validator
    # gate). Set True only when the mission scope was ``final_submission``
    # AND the final reviewer verdict certified the whole project complete
    # (status=done, scope=final_submission, every checklist item satisfied
    # with evidence). The supervisor uses this — never raw ``success`` — to
    # decide whole-project completion. ``completion_evidence`` carries the
    # reviewer's completion summary for the journal.
    final_submission_certified: bool = False
    completion_evidence: str = ""
    # Reviewer-authored structured briefing for the project planner. Shape:
    # ``{"forward_progress": bool, "headline": str, "blocker": str,
    # "recommended_next": str}``. Empty dict when no reviewer verdict exists.
    planner_report: dict = field(default_factory=dict)


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

    def __init__(self) -> None:
        self.workdir: Path | None = None

    @staticmethod
    def _write_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _materialize_research_bootstrap_seed(self, objective: str) -> None:
        workdir = self.workdir
        if workdir is None:
            return
        root = Path(workdir).expanduser()
        root.mkdir(parents=True, exist_ok=True)

        git_dir = root / ".git"
        if not git_dir.exists():
            try:
                subprocess.run(
                    ["git", "init"],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except OSError:
                pass
        if not git_dir.exists():
            git_dir.mkdir(parents=True, exist_ok=True)

        title = root.name.replace("-", " ").strip() or "project"
        state_path = root / "research" / "PIPELINE_STATE.json"
        brief_path = root / "research" / "RESEARCH_BRIEF.md"
        plan_path = root / "research" / "EXPERIMENT_PLAN.md"
        claims_path = root / "research" / "CLAIMS_TO_TEST.md"
        go_no_go_path = root / "research" / "GO_NO_GO.md"
        benchmark_path = root / "experiments" / "BENCHMARK_PROVENANCE.md"

        if not state_path.exists():
            state = {
                "current_stage": "plan",
                "mission_type": "research-bootstrap",
                "project": title,
                "objective": objective,
                "target_venue": "EMNLP",
                "stages": {
                    "research": {
                        "status": "done",
                        "artifact": "research/RESEARCH_BRIEF.md",
                    },
                    "plan": {
                        "status": "ready",
                        "artifact": "research/EXPERIMENT_PLAN.md",
                    },
                    "benchmark": {
                        "status": "ready",
                        "artifact": "experiments/BENCHMARK_PROVENANCE.md",
                    },
                    "run": {"status": "missing"},
                    "analysis": {"status": "missing"},
                    "draft": {"status": "missing"},
                    "review": {"status": "missing"},
                    "submission": {"status": "missing"},
                },
            }
            self._write_text(
                state_path,
                json.dumps(state, indent=2, sort_keys=True) + "\n",
            )
        if not brief_path.exists():
            self._write_text(
                brief_path,
                "\n".join(
                    [
                        "# Research Brief",
                        "",
                        f"- Project: `{root.name}`",
                        "- Bootstrap mode: research seed",
                        f"- Objective: {objective}",
                        "",
                        "This repository was initialized as a research bootstrap mission.",
                        "The next steps are to confirm the benchmark, formalize the claims,",
                        "and move the pipeline ledger from seed state into an executable plan.",
                        "",
                    ]
                ),
            )
        if not plan_path.exists():
            self._write_text(
                plan_path,
                "\n".join(
                    [
                        "# Experiment Plan",
                        "",
                        "## Goal",
                        "- Turn the bootstrap objective into a testable research plan.",
                        "",
                        "## Immediate steps",
                        "1. Choose or confirm the benchmark source and access rules.",
                        "2. Rewrite the objective into falsifiable claims.",
                        "3. Define the evaluation protocol, metrics, and acceptance criteria.",
                        "4. Collect the artifacts needed to advance the pipeline ledger.",
                        "",
                        "## Risks",
                        "- The benchmark may be underspecified.",
                        "- Claims may be too broad for the available evidence.",
                        "",
                    ]
                ),
            )
        if not claims_path.exists():
            self._write_text(
                claims_path,
                "\n".join(
                    [
                        "# Claims To Test",
                        "",
                        "- The system can support a concrete EMNLP-style research workflow.",
                        "- The chosen benchmark and protocol can be documented without fabrication.",
                        "- The pipeline can produce reproducible research artifacts from an empty repo.",
                        "",
                        "Each claim should eventually be paired with a raw artifact path.",
                        "",
                    ]
                ),
            )
        if not go_no_go_path.exists():
            self._write_text(
                go_no_go_path,
                "\n".join(
                    [
                        "# Go / No-Go",
                        "",
                        "- Verdict: blocked",
                        "- Reason: this is only the bootstrap seed; benchmark selection,",
                        "  claim validation, and evidence collection are still pending.",
                        "",
                    ]
                ),
            )
        if not benchmark_path.exists():
            self._write_text(
                benchmark_path,
                "\n".join(
                    [
                        "# Benchmark Provenance",
                        "",
                        "- Status: seed placeholder",
                        f"- Project: `{root.name}`",
                        "- Benchmark source: to be selected",
                        "- Access notes: to be confirmed",
                        "- Filtering or sampling rules: to be defined",
                        "",
                    ]
                ),
            )

    def _materialize_bootstrap_skeleton(self, objective: str) -> None:
        workdir = self.workdir
        if workdir is None:
            return
        # Whether (and which kind of) scaffold to seed is decided by the
        # STRUCTURED preflight + research profile, never by sniffing the
        # objective text for keywords like "emnlp" / "auto-research". The
        # harness must not guess mission type from prose — that is the agent's
        # call, and the research scaffold is opt-in via a configured profile.
        from ..core.bootstrap import inspect_project_bootstrap
        from ..life.research_profile import load_research_profile

        root = Path(workdir).expanduser()
        preflight = inspect_project_bootstrap(root)
        if not preflight.should_bootstrap:
            return
        if load_research_profile() is not None:
            self._materialize_research_bootstrap_seed(objective)
            return
        root.mkdir(parents=True, exist_ok=True)

        git_dir = root / ".git"
        if not git_dir.exists():
            try:
                subprocess.run(
                    ["git", "init"],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except OSError:
                pass
        if not git_dir.exists():
            git_dir.mkdir(parents=True, exist_ok=True)

        package_slug = re.sub(r"[^a-z0-9]+", "_", root.name.lower()).strip("_") or "project"
        pyproject = root / "pyproject.toml"
        if not pyproject.exists():
            pyproject.write_text(
                "\n".join(
                    [
                        "[build-system]",
                        'requires = ["setuptools>=68", "wheel"]',
                        'build-backend = "setuptools.build_meta"',
                        "",
                        "[project]",
                        f'name = "{package_slug.replace("_", "-")}"',
                        'version = "0.1.0"',
                        'description = "Bootstrap package."',
                        'readme = "README.md"',
                        'requires-python = ">=3.10"',
                        "",
                        "[tool.setuptools]",
                        'package-dir = {"" = "src"}',
                        "",
                        "[tool.setuptools.packages.find]",
                        'where = ["src"]',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        readme = root / "README.md"
        if not readme.exists():
            readme.write_text(
                f"# {root.name}\n\nMinimal Python package bootstrap.\n",
                encoding="utf-8",
            )
        package_init = root / "src" / package_slug / "__init__.py"
        if not package_init.exists():
            package_init.parent.mkdir(parents=True, exist_ok=True)
            package_init.write_text(
                f'"""{root.name} package."""\n',
                encoding="utf-8",
            )
        smoke_test = root / "tests" / "test_smoke.py"
        if not smoke_test.exists():
            smoke_test.parent.mkdir(parents=True, exist_ok=True)
            smoke_test.write_text(
                "def test_package_import():\n"
                f"    import {package_slug}\n\n"
                f"    assert {package_slug}.__name__ == \"{package_slug}\"\n",
                encoding="utf-8",
            )

    def execute(
        self,
        *,
        objective: str,
        sink: EventSink,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
        seed_thread_id: str | None = None,  # noqa: ARG002 — protocol parity
        scope: str = "",  # noqa: ARG002 — protocol parity
    ) -> _Outcome:
        self._materialize_bootstrap_skeleton(objective)
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


_TEST_DAEMON_PLANNER_SCRIPT_ENV = "ARGUS_SKILL_DAEMON_TEST_PLANNER_SCRIPT"


class _ScriptedPlannerBackend:
    """Test-only planner backend for daemon continuous-mode integration."""

    def __init__(self, *, planner: list[dict[str, Any]], critic: list[dict[str, Any]]) -> None:
        self._planner = list(planner)
        self._critic = list(critic)

    @classmethod
    def from_env(cls) -> "_ScriptedPlannerBackend | None":
        raw_path = os.environ.get(_TEST_DAEMON_PLANNER_SCRIPT_ENV, "").strip()
        if not raw_path:
            return None
        path = Path(raw_path).expanduser()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise SystemExit(
                f"argus-skill: failed to read scripted planner backend: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise SystemExit(
                "argus-skill: scripted planner backend must be a JSON object"
            )
        planner = data.get("planner", [])
        critic = data.get("critic", [])
        if not isinstance(planner, list) or not isinstance(critic, list):
            raise SystemExit(
                "argus-skill: scripted planner backend requires planner/critic arrays"
            )
        return cls(planner=planner, critic=critic)

    def _pop(self, queue: list[dict[str, Any]], *, kind: str, run_label: str) -> dict[str, Any]:
        if not queue:
            raise RuntimeError(
                f"argus-skill: scripted planner backend exhausted for {kind} ({run_label})"
            )
        payload = queue.pop(0)
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"argus-skill: scripted planner backend entry for {kind} must be an object"
            )
        delay_seconds = payload.get("delay_seconds", 0)
        try:
            delay = float(delay_seconds)
        except (TypeError, ValueError):
            delay = 0.0
        if delay > 0:
            time.sleep(delay)
        return payload

    def run_exec(
        self,
        *,
        prompt,
        options,
        run_label,
        resume_thread_id=None,
        **kw,
    ) -> RunnerResult:  # noqa: ANN001, D417
        del prompt, options, resume_thread_id, kw
        if str(run_label).startswith("planner."):
            payload = self._pop(self._planner, kind="planner", run_label=str(run_label))
        elif str(run_label).startswith("critic."):
            payload = self._pop(self._critic, kind="critic", run_label=str(run_label))
        else:
            raise RuntimeError(
                f"argus-skill: scripted planner backend cannot handle {run_label!r}"
            )
        return RunnerResult(exit_code=0, agent_messages=[json.dumps(payload, ensure_ascii=False)])


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
        from ..adapters.codex_backend import _strip_legacy_codex_profile_args
        backend_name = os.environ.get("ARGUS_SKILL_RUNNER_BACKEND") or None
        runner_bin = os.environ.get("ARGUS_SKILL_RUNNER_BIN") or None
        raw_extra = os.environ.get("ARGUS_SKILL_RUNNER_EXTRA_ARGS", "").strip()
        extra = _strip_legacy_codex_profile_args(
            shlex.split(raw_extra) if raw_extra else None
        )
        stop_event = getattr(args, "stop_event", None)

        def _stop_reason() -> str | None:
            if stop_event is not None and stop_event.is_set():
                return "daemon stop requested"
            return None

        self._backend = CodexRunnerBackend(
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
        self._args = args
        # Session continuity: seed_thread_id is the codex session id from
        # the previous mission in the same REPL session. We propagate it
        # into the *first* engineer round of this mission, then update
        # in-place after each execute() so the chat REPL can recover the
        # latest thread_id and forward it to the next mission.
        self._next_seed_thread_id: str | None = seed_thread_id
        self.last_thread_id: str | None = seed_thread_id
        # Chat fast-path is operator-REPL-only: enabled per-invocation by
        # ``_invoke_supervisor`` for human free-text typed at the cockpit.
        # Defaults False so planner / backlog / daemon missions are never
        # classified — the harness must not second-guess agent-produced work.
        self._allow_chat_fast_path: bool = False

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
        scope: str = "",
    ) -> _Outcome:
        # Chat fast-path (operator-REPL-only; gated by _allow_chat_fast_path).
        # Conversational input (greetings, capability questions, acks) doesn't
        # need matcher → distill → engineer round-loop → reviewer. A trace
        # before this guard: "hello" cost $0.10 + 72s, ran `pwd && ls && rg
        # --files && sed README.md`, then the reviewer rejected it for "doing
        # unrelated repo inspection". A cheap model call classifies the message
        # and, on a clear CHAT answer, short-circuits to a single chat-prompt
        # codex call — no skill machinery, no reviewer, no writeback.
        # Cost note: this classifier runs only on interactive operator free
        # text (never the 7×24 daemon), so its tiny low-reasoning call is not
        # part of autonomous spend and is not separately metered.
        if self._allow_chat_fast_path:
            from ..core.models import RunnerOptions
            from ..life.router import classify_is_conversational

            _safe_mode = _env_flag("ARGUS_SKILL_SAFE_MODE", False)
            _workdir = (
                Path(self._args.workdir).expanduser()
                if getattr(self._args, "workdir", None)
                else Path.cwd()
            )

            def _classify_run_exec(prompt: str) -> Any:
                return self._backend.run_exec(
                    prompt=prompt,
                    options=RunnerOptions(
                        model=self._args.engineer_model,
                        reasoning_effort="low",
                        full_auto=_safe_mode,
                        skip_git_repo_check=True,
                        dangerous_yolo=not _safe_mode,
                        working_dir=str(_workdir),
                    ),
                    run_label="router-classify",
                    resume_thread_id=None,
                )

            if classify_is_conversational(objective, run_exec=_classify_run_exec):
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
        safe_mode = _env_flag("ARGUS_SKILL_SAFE_MODE", False)
        benchmark_mode = _env_flag("ARGUS_SKILL_BENCHMARK_MODE", False)
        benchmark_verifier_gate = _env_flag(
            "ARGUS_SKILL_BENCHMARK_VERIFIER_GATE", False
        )
        if benchmark_mode and (
            benchmark_verifier_gate
            or _env_flag("ARGUS_SKILL_NO_REVIEWER", False)
        ):
            return self._benchmark_direct_execute(
                objective=objective,
                sink=sink,
                prelude_context=prelude_context,
                seed_thread_id=seed_thread_id,
                safe_mode=safe_mode,
            )
        config_kwargs = {
            "scientist_model": args.scientist_model,
            "engineer_model": args.engineer_model,
            "reviewer_model": args.reviewer_model,
            "scientist_reasoning_effort": getattr(
                args,
                "scientist_reasoning_effort",
                "high",
            ),
            "engineer_reasoning_effort": getattr(
                args, "engineer_reasoning_effort", "high"
            ),
            "reviewer_reasoning_effort": getattr(
                args,
                "reviewer_reasoning_effort",
                "high",
            ),
            "max_rounds": args.max_rounds,
            "check_commands": list(getattr(args, "check_commands", []) or []),
            "skill_writeback": _env_flag(
                "ARGUS_SKILL_SKILL_WRITEBACK",
                default=not benchmark_mode,
            ),
            "distill_on_miss": _env_flag(
                "ARGUS_SKILL_DISTILL_ON_MISS",
                default=not benchmark_mode,
            ),
            "skill_revise_on_failure": _env_flag(
                "ARGUS_SKILL_SKILL_REVISE_ON_FAILURE",
                default=not benchmark_mode,
            ),
            "dangerous_yolo": not safe_mode,
            "full_auto": safe_mode,
            "skip_git_repo_check": True,
            # Explicit paper-mission signal (replaces objective keyword sniffing).
            # Defaults True because this runner is the life/EMNLP execution path;
            # an operator can pass ``--no-paper-mission`` to turn it off.
            "paper_mission": getattr(args, "paper_mission", True),
            # Persist the curated working-memory checkpoint to the per-project
            # state dir so the reviewer handoff survives across missions and
            # daemon restarts (cross-session continuity).
            "checkpoint_path": _checkpoint_path_for(
                args,
                Path(args.workdir).expanduser() if args.workdir else Path.cwd(),
            ),
        }
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
        loop = self._SkillLoop(
            skills_dir=Path(args.skills_dir),
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
        # Scope is threaded structurally from the planner via the backlog
        # item's tags (LifeSupervisor passes _planner_scope_from_item(item)).
        # We no longer re-parse it out of the objective prose — the harness
        # should consume the structured field, not sniff the rendered text.
        mission_scope = (scope or "").strip().lower()
        try:
            outcome = loop.run(
                full_task, workdir=workdir, seed_thread_id=seed,
                failed_tool_ledger=ledger,
                objective_for_skill=objective,
                scope=mission_scope,
            )
        finally:
            self._current_sink = None
            self._current_failure_ledger = None
        new_tid = getattr(outcome, "last_thread_id", None)
        if should_clear_thread_id_after_outcome(
            status=str(getattr(outcome, "status", "")),
            fatal_error=str(getattr(outcome, "stop_reason", "") or ""),
        ):
            self.last_thread_id = None
            self._next_seed_thread_id = None
            new_tid = None
        elif new_tid:
            self.last_thread_id = new_tid
            self._next_seed_thread_id = new_tid
        auth_fail = getattr(self._backend, "_auth_failure_detected", False)
        if auth_fail:
            self._backend._auth_failure_detected = False
        # Reviewer completion contract: certify whole-project completion only
        # from the final reviewer verdict (never raw success). Fail-closed:
        # absent rounds / review / non-final scope ⇒ not certified.
        final_submission_certified = False
        completion_evidence = ""
        # Pull the reviewer's structured planner briefing off the final round
        # so the supervisor can journal it for the project planner verbatim.
        planner_report: dict = {}
        rounds_list = getattr(outcome, "rounds", None) or []
        if rounds_list:
            _final_review = getattr(rounds_list[-1], "review", None)
            if _final_review is not None:
                report = getattr(_final_review, "planner_report", None)
                if isinstance(report, dict):
                    planner_report = report
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
        return _Outcome(
            success=outcome.successful,
            status=outcome.status,
            stop_reason=outcome.reason or "",
            rounds=outcome.round_count,
            matched_skill_name=outcome.skill_used,
            skill_distilled=outcome.skill_distilled,
            last_thread_id=new_tid,
            auth_failure=auth_fail,
            final_submission_certified=final_submission_certified,
            completion_evidence=completion_evidence,
            planner_report=planner_report,
        )

    def _benchmark_direct_execute(
        self,
        *,
        objective: str,
        sink: EventSink,
        prelude_context: str = "",
        seed_thread_id: str | None = None,
        safe_mode: bool = False,
    ) -> _Outcome:
        """Run one prompt-only benchmark turn and leave correctness to the verifier."""
        from ..core.models import RunnerOptions

        args = self._args
        seed = self._next_seed_thread_id if seed_thread_id is None else seed_thread_id
        workdir = (
            Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        )
        guidance = ""
        if _env_flag("ARGUS_SKILL_BENCHMARK_TERSE", False):
            guidance = (
                "## Benchmark lean-mode instructions\n"
                "- Solve the task autonomously in one engineer turn.\n"
                "- The official verifier will judge correctness; do not run an internal review.\n"
                "- Minimize narration and final prose. Prefer concise progress, batched shell commands, and stop after the required artifact is produced and self-checked.\n"
            )
        parts = [part for part in (prelude_context, guidance, f"## Live objective\n{objective}") if part]
        prompt = "\n---\n".join(parts)

        sink.handle_event({
            "type": "loop.start",
            "text": f"benchmark-direct: {objective[:80]}",
            "benchmark_mode": True,
        })
        sink.handle_event({
            "type": "round.start",
            "round": 1,
            "round_max": 1,
            "text": "engineer round 1 (benchmark direct)",
        })

        self._current_sink = sink
        self._current_failure_ledger = None
        try:
            result = self._backend.run_exec(
                prompt=prompt,
                options=RunnerOptions(
                    model=args.engineer_model,
                    reasoning_effort=getattr(args, "engineer_reasoning_effort", None),
                    full_auto=safe_mode,
                    skip_git_repo_check=True,
                    dangerous_yolo=not safe_mode,
                    working_dir=str(workdir),
                ),
                run_label="benchmark-engineer-r1",
                resume_thread_id=seed,
            )
        finally:
            self._current_sink = None

        last_msg = (result.last_agent_message or "").strip()
        new_tid = getattr(result, "thread_id", None)
        round_thread_id = new_tid or seed
        status = "error" if getattr(result, "exit_code", 0) != 0 else "done"
        if should_clear_thread_id_after_outcome(
            status=status,
            fatal_error=str(getattr(result, "fatal_error", "") or ""),
        ):
            self.last_thread_id = None
            self._next_seed_thread_id = None
            new_tid = None
        elif new_tid:
            self.last_thread_id = new_tid
            self._next_seed_thread_id = new_tid

        sink.handle_event({
            "type": "round.main.completed",
            "round_index": 1,
            "round_max": 1,
            "session_id": round_thread_id,
            "exit_code": getattr(result, "exit_code", 0),
            "fatal_error": getattr(result, "fatal_error", None),
            "last_message": last_msg,
            "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
            "cached_input_tokens": int(
                getattr(result, "cached_input_tokens", 0) or 0
            ),
            "output_tokens": int(getattr(result, "output_tokens", 0) or 0),
            "usage_scope": "delta",
        })

        fatal = getattr(result, "fatal_error", None)
        success = (result.exit_code == 0) and not fatal
        status = "done" if success else "error"
        stop_reason = "benchmark_direct" if success else (
            str(fatal) if fatal else f"exit={result.exit_code}"
        )
        auth_fail = getattr(self._backend, "_auth_failure_detected", False)
        if auth_fail:
            self._backend._auth_failure_detected = False
        sink.handle_event({
            "type": "loop.done",
            "text": f"status={status} rounds=1 (benchmark direct)",
        })
        return _Outcome(
            success=success,
            status=status,
            stop_reason=stop_reason,
            rounds=1,
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
        safe_mode = _env_flag("ARGUS_SKILL_SAFE_MODE", False)
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
                    reasoning_effort=getattr(args, "engineer_reasoning_effort", "high"),
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
        round_thread_id = new_tid or seed
        status = "error" if getattr(result, "exit_code", 0) != 0 else "done"
        if should_clear_thread_id_after_outcome(
            status=status,
            fatal_error=str(getattr(result, "fatal_error", "") or ""),
        ):
            self.last_thread_id = None
            self._next_seed_thread_id = None
            new_tid = None
        elif new_tid:
            self.last_thread_id = new_tid
            self._next_seed_thread_id = new_tid

        # ``round.main.completed`` is the event the cost-tracking sink
        # listens to for engineer-side tokens. Emitting it here keeps
        # the chat fast-path's USD figure honest.
        sink.handle_event({
            "type": "round.main.completed",
            "round_index": 1,
            "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
            "cached_input_tokens": int(
                getattr(result, "cached_input_tokens", 0) or 0
            ),
            "output_tokens": int(getattr(result, "output_tokens", 0) or 0),
            "usage_scope": "delta",
            "last_message": last_msg,
            "session_id": round_thread_id,
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
        return ("bundled codex_autoloop module not importable — "
                "reinstall argus-skill")
    try:  # noqa: SIM105
        _import_argusbot()
    except Exception:  # noqa: BLE001
        return ("bundled codex_autoloop failed to load — "
                "check the argus-skill install")
    import shutil
    bin_path = os.environ.get("ARGUS_SKILL_RUNNER_BIN") or shutil.which("codex")
    if not bin_path:
        return ("`codex` binary not found on PATH — install with "
                "`npm install -g @openai/codex` or set ARGUS_SKILL_RUNNER_BIN")
    return None


def _inbox_drainer_for(life_dir: Path):
    """Return a `user_inbox` callable that drains pending messages from
    ``<life_dir>/inbox.jsonl``.

    The CLI's ``argus-skill --notify "<msg>"`` and the REPL's ``/nudge``
    slash command both append to this file. Each call to the returned
    callable returns one message (or ``None``) and advances a tiny
    offset file so the same line is never replayed twice.
    """
    from ._inbox import drain_inbox_messages

    def _drain_one() -> str | None:
        try:
            messages = drain_inbox_messages(life_dir, limit=1)
        except Exception:  # noqa: BLE001
            return None
        return messages[0] if messages else None

    return _drain_one


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
    project_worktree: Path | None = None,
    quiet: bool = False,
    runtime_context: str = "",
    continuous: bool = False,
    continuous_objective: str = "",
    open_ended: bool = True,
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
        from ..life.telemetry import telemetry_interval_from_env

        stderr_sink = LifeStderrSink(quiet=quiet)
        project_root = _memory_project_root(mem)
        sink = JsonlEventSink(stderr_sink, life_dir=project_root)
        cfg = LifeSupervisorConfig(
            budget=LifeBudget(
                per_mission_cap_usd=per_mission_cap_usd,
                daily_cap_usd=daily_cap_usd,
                max_missions=1 if once else max_missions,
            ),
            poll_interval_seconds=2.0,
            project_worktree=(
                Path(project_worktree).expanduser()
                if project_worktree is not None
                else None
            ),
            stop_event=stop_event,
            user_inbox=_inbox_drainer_for(project_root),
            runtime_context=runtime_context,
            continuous=continuous,
            continuous_objective=continuous_objective,
            open_ended=open_ended,
            # M0.4 (same as daemon life_worker.py): bounded mode disables
            # the full EMNLP pipeline gate.
            full_emnlp_gate=not open_ended,
            telemetry_dir=project_root,
            telemetry_interval_seconds=telemetry_interval_from_env(),
        )
        sup = LifeSupervisor(
            memory=mem,
            runner=runner,
            sink=sink,
            config=cfg,
            engineer_model=engineer_model,
            reviewer_model=reviewer_model,
            planner_runner=getattr(runner, "backend", None),
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
    open_ended: bool = True,
    allow_chat_fast_path: bool = False,
) -> tuple[dict[str, Any], str | None]:
    ns = argparse.Namespace()
    ns.backend = backend
    benchmark_mode = _env_flag("ARGUS_SKILL_BENCHMARK_MODE", False)
    from ..tools.capability_vault import resolve_route_model

    ns.engineer_model = os.environ.get("ARGUS_SKILL_ENGINEER_MODEL") or resolve_route_model(
        "engineer"
    )
    reviewer_default = ns.engineer_model if benchmark_mode else resolve_route_model("reviewer")
    ns.reviewer_model = os.environ.get("ARGUS_SKILL_REVIEWER_MODEL") or reviewer_default
    ns.scientist_model = os.environ.get("ARGUS_SKILL_SCIENTIST_MODEL") or resolve_route_model(
        "scientist"
    )
    ns.scientist_reasoning_effort = os.environ.get(
        "ARGUS_SKILL_SCIENTIST_REASONING_EFFORT",
        "high",
    )
    ns.engineer_reasoning_effort = os.environ.get(
        "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
        "high",
    )
    ns.reviewer_reasoning_effort = os.environ.get(
        "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
        "high",
    )
    ns.skills_dir = os.environ.get(
        "ARGUS_SKILL_SKILLS_DIR",
        str(_memory_global_root(mem) / "skills"),
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
        f"- Scientist model: {ns.scientist_model}\n"
        f"- Engineer reasoning effort: {ns.engineer_reasoning_effort or '(default)'}\n"
        f"- Reviewer reasoning effort: {ns.reviewer_reasoning_effort or '(default)'}\n"
        f"- Scientist reasoning effort: {ns.scientist_reasoning_effort or '(default)'}\n"
        f"- Max rounds per mission: {ns.max_rounds}\n"
        f"- Per-mission budget cap: ${per_mission_cap_usd:.2f}\n"
        f"- Daily budget cap: ${daily_cap_usd:.2f}\n"
        f"- Mode: {mode_label}\n"
        f"- Benchmark lean mode: {'on' if benchmark_mode else 'off'}\n"
    )
    from ..life.research_profile import render_research_profile_context

    research_context = render_research_profile_context()
    if research_context:
        runtime_context = runtime_context + "\n---\n\n" + research_context

    runner = build_life_runner(ns, seed_thread_id=seed_thread_id)
    # Chat fast-path is operator-REPL-only: only human free text typed at the
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
        project_worktree=getattr(mem, "project_worktree", None) or Path.cwd(),
        quiet=quiet,
        runtime_context=runtime_context,
        continuous=continuous,
        continuous_objective=continuous_objective,
        open_ended=open_ended,
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
    return parse_add_flags(
        text,
        default_iterate=default_iterate,
        default_cycles=default_cycles,
        default_budget=default_budget,
    )


def _add_only(
    mem: _CommonMemory,
    text: str,
    *,
    priority: int = 100,
    iterate: bool = True,
    iteration_max_cycles: int = 6,
    iteration_budget_usd: float = 30.0,
) -> BacklogItem:
    item = add_backlog_item(
        mem,
        text,
        priority=priority,
        iterate=iterate,
        iteration_max_cycles=iteration_max_cycles,
        iteration_budget_usd=iteration_budget_usd,
    )
    print(format_added_item(item), flush=True)
    return item


def _backend_cmd(tokens: list[str], chat_state: dict[str, Any]) -> None:
    print(render_backend_cmd(tokens, chat_state))


def _continuous_session_error(
    backend: str,
    continuous: bool,
    objective: str,
) -> str:
    return _shared_continuous_session_error(backend, continuous, objective)


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
    print(render_config_cmd(tokens, chat_state, life_dir=life_dir))


def _identity_cmd(mem: _CommonMemory, tokens: list[str], rest_text: str) -> None:
    if not tokens:
        print(render_identity_cmd(mem, tokens, rest_text, empty_hint="edit"))
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
    print(render_identity_cmd(mem, tokens, rest_text))


def _project_cmd(mem: _CommonMemory, tokens: list[str], rest_text: str) -> None:
    print(render_project_cmd(mem, tokens, rest_text))


def _continuous_cmd(
    mem: _SplitMemory,
    arg_text: str,
    chat_state: dict[str, Any],
) -> None:
    from ..daemon.life_worker import (
        ContinuousConfigState,
        continuous_mode_error,
        read_continuous_config,
        read_continuous_state,
        write_continuous_config,
    )

    tokens = shlex.split(arg_text) if arg_text.strip() else []
    sub = tokens[0].lower() if tokens else "status"
    backend = str(chat_state.get("backend", "") or "codex")

    state = chat_state.get("continuous_state")
    if isinstance(state, ContinuousConfigState):
        current_objective = state.objective
    else:
        _, current_objective = read_continuous_config(mem.project.root)

    if sub in {"start", "on", "enable"}:
        objective = " ".join(tokens[1:]).strip() or current_objective
        error = continuous_mode_error(backend, True, objective)
        if error:
            print(error)
            return
        write_continuous_config(mem.project.root, enabled=True, objective=objective)
        updated = read_continuous_state(mem.project.root)
        chat_state["continuous_state"] = updated
        chat_state["continuous_objective"] = updated.objective
        chat_state.setdefault("config", dict(_CONFIG_DEFAULTS))["continuous"] = True
        print(
            f"continuous: on\n"
            f"objective: {updated.objective or '(none)'}"
        )
        return

    if sub in {"stop", "off", "pause"}:
        objective = " ".join(tokens[1:]).strip() or current_objective
        write_continuous_config(mem.project.root, enabled=False, objective=objective)
        updated = read_continuous_state(mem.project.root)
        chat_state["continuous_state"] = updated
        chat_state["continuous_objective"] = updated.objective
        chat_state.setdefault("config", dict(_CONFIG_DEFAULTS))["continuous"] = False
        print(
            f"continuous: off\n"
            f"objective: {updated.objective or '(none)'}"
        )
        return

    enabled, objective = read_continuous_config(mem.project.root)
    chat_state["continuous_state"] = ContinuousConfigState(
        enabled=enabled,
        objective=objective,
    )
    chat_state["continuous_objective"] = objective
    print(
        f"continuous: {'on' if enabled else 'off'}\n"
        f"objective: {objective or '(none)'}"
    )


def _backlog_list_cmd(mem: _CommonMemory, *, include_all: bool) -> None:
    print(format_backlog_list(mem, include_all=include_all))


def _status_change_cmd(mem: _CommonMemory, cmd: str, item_id: str) -> None:
    print(format_status_change(mem, cmd, item_id))


def _journal_tail_cmd(mem: _CommonMemory, n: int) -> None:
    print(format_journal_tail(mem, n))


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
    continuous improvement mode: the planner inspects the
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
        open_ended=chat_state.get("open_ended", True),
        # Only single-shot operator free text is chat-eligible. In continuous
        # mode the typed text is a mission objective (and later missions are
        # planner work), so classification stays off.
        allow_chat_fast_path=not continuous,
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
    open_ended: bool = True,
    allow_chat_fast_path: bool = False,
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
        open_ended=open_ended,
        allow_chat_fast_path=allow_chat_fast_path,
    )
    elapsed = time.monotonic() - t0
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
    output = render_run_command(mem, opts, chat_state)
    if not output:
        return
    print(output)


def _status_cmd(mem: _SplitMemory, chat_state: dict[str, Any] | None = None) -> None:
    """Lightweight status print (mirrors `argus-skill life status` output)."""
    from ..daemon.life_worker import ContinuousConfigState, read_continuous_state
    from ._inbox import count_pending_inbox_messages

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
    print(f"inbox   : {count_pending_inbox_messages(mem.project.root)} pending")
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
        ("/project [set …]", "view or update the project card"),
        ("/start [objective]", "enable continuous mode "
                              "(alias of /continuous start)"),
        ("/continuous start|stop [objective]", "control continuous mode"),
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
        "In continuous mode the planner inspects the project after each "
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
    print(render_skills_cmd(Path.cwd(), tokens))


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
    if cli_objective and not cli_continuous:
        error = _continuous_session_error(backend_default, False, cli_objective)
        if error:
            return {}, error

    if cli_continuous:
        continuous = True
        objective = cli_objective
        error = _continuous_session_error(backend_default, continuous, objective)
        if error:
            return {}, error
    else:
        objective = disk_objective if disk_state.enabled else ""
        continuous = disk_state.enabled
        if continuous and _continuous_session_error(backend_default, True, objective):
            continuous = False

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
        # Lifetime semantics: keep generating work after project_done unless
        # the operator launched with --bounded.
        "open_ended": not bool(getattr(args, "bounded", False)),
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
    try:
        global_root = _resolve_global_root(args)
        mem: MemoryBundle = MemoryBundle.for_cwd(Path.cwd(), global_root=global_root)
    except core_paths.PathResolutionError as exc:
        sys.stderr.write(f"argus-skill: {exc}\n")
        return 2
    state = mem.init()
    created: list[str] = []
    for scope, rows in state.items():
        for name, was_created in rows.items():
            if was_created:
                created.append(f"{scope}.{name}")
    theme = None  # populated in the locked body

    # Fail fast before we take the singleton lock if the current run
    # explicitly requests continuous mode that the backend cannot satisfy.
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
        if cmd == "/start":
            _continuous_cmd(mem, f"start {rest_text}".strip(), chat_state)
            continue
        if cmd == "/continuous":
            _continuous_cmd(mem, rest_text, chat_state)
            continue
        if cmd == "/identity":
            _identity_cmd(mem, rest, rest_text)
            continue
        if cmd == "/project":
            _project_cmd(mem, rest, rest_text)
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
            print(stop_iteration(mem, rest[0]))
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
            print(theme.gray(append_note(mem, rest_text)))
            continue
        if cmd in ("/nudge", "/inject", "/notify"):
            if not rest_text:
                print(theme.gray("usage: /nudge <message>  (one line, "
                                 "spliced into the next engineer round)"))
                continue
            from ._inbox import queue_inbox_message
            queue_inbox_message(mem.project.root, rest_text, source="repl.nudge")
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
            print(theme.gray(render_reset_cmd(chat_state)))
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
