"""Lifetime-agent runtime infrastructure (backend-neutral).

This module owns the non-interactive machinery the daemon, teammate
runner, and the Manager REPL all share:

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

History: these lived in ``apps/_life_repl/`` mixed together with the
interactive REPL. The REPL conversation surface moved to
``manager/repl.py``; the infrastructure below moved here so the daemon
and teammate paths never import the interactive layer.
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
from pathlib import Path
from typing import Any, Callable, ClassVar, Protocol

from ..core import paths as core_paths  # noqa: F401 — re-exported convenience
from ..core.models import RunnerResult
from ..core.ports import EventSink
from ..engineer.runner import should_clear_thread_id_after_outcome
from ..life import BacklogItem  # noqa: F401 — re-exported convenience
from ..life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)
from ._target_paths import resolve_life_root

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env helpers + memory protocols (formerly _life_repl/_base.py)
# ---------------------------------------------------------------------------


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
    # Reviewer → Planner checklist feedback from the final round (advisory; the
    # reviewer never edits the checklist). Surfaced in the reviewer→planner
    # journal block so the project Planner can act on it (via checklist_ops) next
    # cycle. Empty dict when the reviewer raised no checklist complaint.
    checklist_feedback: dict = field(default_factory=dict)
    # Reviewer → Planner STEP-BACK reflection from the final round (the anti-
    # plan-lock-in channel). Authored on EVERY round with a measured result —
    # including a clean success — surfacing new questions / alternative
    # directions the planner must triage (rule 17d). ``None`` when the round had
    # no measured result or the reviewer omitted it. Shape: see
    # ``ReviewDecision.step_back``.
    step_back: dict | None = None
    # Reviewer-judged reusable PROCESS lesson from the final round (the agent's
    # own self-evolution signal — how it worked, where it wasted/repeated rounds,
    # a workaround that helped). Distinct from the research METHOD. The supervisor
    # journals this as ``self_evolve.process_lesson`` so future missions can learn
    # from accumulated process data. Empty when the round had nothing reusable.
    process_lesson: str = ""
    # The Manager's stage-transition verdict for this mission completion (the
    # Manager is the sole post-bootstrap writer of current_stage). Shape:
    # ``{"action": advance|hold|rollback, "target_stage", "reason",
    # "current_stage", "source"}``. Empty dict when the decision
    # was skipped (error) or never ran. Journaled by the supervisor; the stage
    # write itself already happened inside execute.
    stage_transition: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Runner adapters (formerly _life_repl/_runners.py)
# ---------------------------------------------------------------------------


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
        original_objective: str = "",
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


class _SkillLoopRunner:
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

        # Mirror build_agent_cli_backend_from_env's env-var contract here so
        # we can also pass event_callback (the helper doesn't expose it).
        from ..adapters.agent_cli_backend import _strip_legacy_codex_profile_args
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
            be_env = os.environ.get(f"ARGUS_SKILL_{role.upper()}_BACKEND", "").strip()
            bin_env = os.environ.get(
                f"ARGUS_SKILL_{role.upper()}_RUNNER_BIN", ""
            ).strip()
            if not be_env and not bin_env:
                return self._backend
            from ..agent_cli.runner_backend import (
                default_runner_bin,
                normalize_runner_backend,
            )

            chosen = normalize_runner_backend(be_env or backend_name)
            same_type = normalize_runner_backend(backend_name) == chosen
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
        # The ONE Manager instance for this runner. All daemon-side Manager uses
        # (divide / is_conversational / approve_skill) go through this single
        # instance on the manager backend — no more scattered ad-hoc
        # ``Manager(...)`` constructions, and skill approval now genuinely runs
        # on the Manager's backend rather than the reviewer's.
        from ..manager import Manager

        _manager_workdir = (
            Path(args.workdir).expanduser()
            if getattr(args, "workdir", None)
            else Path.cwd()
        )
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
        )
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

    def run_exec(self, **kwargs):
        """Proxy to the manager backend so manager-side skill-library gates can
        run_exec against this runner directly.

        ``Manager.approve_skill`` / ``classify_skill_placement`` pass
        ``runner=(self._session or self.runner)``; on the daemon ``_session`` is
        this ``_SkillLoopRunner``, which had no ``run_exec`` — so both the skill
        gate and placement raised ``AttributeError`` (caught → distillation
        silently no-op'd). Delegate to the same backend the Manager itself uses.
        """
        backend = self.manager_backend or self._backend
        return backend.run_exec(**kwargs)

    def _maybe_chat_outcome(
        self,
        *,
        objective: str,
        sink: EventSink,
        seed_thread_id: str | None = None,
    ) -> "_Outcome | None":
        # Chat fast-path (operator-REPL/Manager-front-end-only).
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
        from ..core.models import RunnerOptions

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

        # The Manager owns the lego-block route decision (chat / simple /
        # complex); the runner only executes it. Route through the runner's
        # single Manager instance (manager backend). This whole fast-path is
        # gated to operator-REPL input (``_allow_chat_fast_path``), so daemon /
        # backlog / planner work NEVER takes chat or simple — it always runs the
        # full pipeline with the reviewer gate. The operator is the reviewer for
        # an interactive simple one-shot.
        route = self.manager.route(objective, run_exec=_classify_run_exec)
        if route == "chat":
            return self._chat_quick_reply(
                objective=objective, sink=sink, seed_thread_id=seed_thread_id,
            )
        if route == "simple":
            return self._simple_quick_reply(
                objective=objective, sink=sink, seed_thread_id=seed_thread_id,
            )
        return None  # complex → full mission pipeline

    def chat_reply_if_conversational(
        self,
        *,
        objective: str,
        sink: EventSink,
        seed_thread_id: str | None = None,
    ) -> bool:
        """Front-end hook: classify + (if chat/simple) reply in-band.

        Used by the Manager REPL front-end to triage free text BEFORE it ever
        reaches the backlog. Returns True iff a direct reply was emitted to
        ``sink`` (so the caller can skip enqueueing); False means "this is
        complex work — enqueue it for the daemon".
        """
        return self._maybe_chat_outcome(
            objective=objective,
            sink=sink,
            seed_thread_id=seed_thread_id,
        ) is not None

    def execute(
        self,
        *,
        objective: str,
        original_objective: str = "",
        sink: EventSink,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
        seed_thread_id: str | None = None,
        scope: str = "",
        per_mission_budget: Any | None = None,
    ) -> _Outcome:
        # Chat fast-path (operator-REPL-only; gated by _allow_chat_fast_path).
        # The classifier + reply logic lives in ``_maybe_chat_outcome``; here we
        # only gate it so the 7×24 daemon (``_allow_chat_fast_path=False``) does
        # not classify arbitrary autonomous work — agent-produced backlog work
        # must not be second-guessed.
        if self._allow_chat_fast_path:
            _chat = self._maybe_chat_outcome(
                objective=objective,
                sink=sink,
                seed_thread_id=seed_thread_id,
            )
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
            "skill_ops_enabled": _env_flag(
                "ARGUS_SKILL_SKILL_OPS",
                default=True,
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
            # Process-correctness audit: the reviewer runs in the project
            # work-tree and only sees the engineer's final summary. Give it the
            # ABSOLUTE path to this project's engineer execution log
            # (``<life_dir>/events.jsonl``, which lives next to checkpoint.json in
            # the per-project state dir — NOT the git work-tree) so it can grep
            # HOW the result was produced. Empty string when checkpoint
            # persistence is off (no resolvable life_dir) → reviewer prompt keeps
            # its legacy shape, byte-for-byte. Filled below once we resolve the
            # checkpoint path.
        }
        _eng_log_ckpt = _checkpoint_path_for(
            args, Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        )
        config_kwargs["engineer_log_path"] = (
            str(_eng_log_ckpt.parent / "events.jsonl") if _eng_log_ckpt is not None else ""
        )
        # paper_mission follows the VERTICAL, not the True default. An optimize
        # vertical (kernelbench / speedrun / nanochat / nanogpt_speedrun) is never
        # a paper mission: force it off so the supervisor picks the lean grind
        # scaffold instead of the research run-stage pilot gate (the source of the
        # "kernel objective → fill PILOT_OPERATOR_DECISION_TEMPLATE.json" misroute).
        try:
            from ..skills.vertical_select import resolve_vertical
            from ..verticals._base import load_vertical, vertical_completion_gate
            _proot = Path(args.workdir).expanduser() if args.workdir else Path.cwd()
            if vertical_completion_gate(load_vertical(resolve_vertical(_proot),
                                                      project_root=_proot)) != "full_emnlp":
                config_kwargs["paper_mission"] = False
        except Exception:  # noqa: BLE001 — fail-soft: keep the default paper_mission
            pass
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
        # Opt-in LONG-TAILED SIMULATED HUMAN OPERATOR (default OFF). When
        # ARGUS_SKILL_SIMULATED_OPERATOR=1 the env-gated helper returns a
        # provider that injects one grounded, long-tailed operator message per
        # engineer round (rendered by the loop as "## Operator guidance"). With
        # the flag OFF it returns None and the loop is built exactly as before,
        # so existing behaviour/tests are unchanged. Never raises into a mission.
        sim_provider = None
        # The per-project state dir (life_dir) holds inbox.jsonl + events.jsonl,
        # next to the reviewer checkpoint.json; derive both from the checkpoint.
        operator_checkpoint_path = _checkpoint_path_for(args, workdir)
        # Only import + build the simulated operator when explicitly enabled, so
        # the 808-LOC test-double in life/operator_sim.py never loads on a real
        # production run (default OFF). Truthiness mirrors
        # operator_sim.simulated_operator_enabled() exactly.
        if os.environ.get("ARGUS_SKILL_SIMULATED_OPERATOR", "").strip().lower() in {
            "1", "true", "yes", "on"
        }:
            try:
                from ..life.operator_sim import operator_guidance_provider_from_env

                # GROUNDING (Bug 1): the run's real telemetry does NOT live in the
                # git work-tree. The daemon/REPL fan events out to
                # ``<life_dir>/events.jsonl`` in the per-project state dir, right
                # next to the per-round reviewer ``checkpoint.json``. So derive the
                # trace path from the checkpoint's parent dir; only fall back to a
                # work-tree-local events.jsonl when checkpoint persistence is off.
                if operator_checkpoint_path is not None:
                    operator_trace_path = operator_checkpoint_path.parent / "events.jsonl"
                else:
                    operator_trace_path = workdir / "events.jsonl"

                sim_provider = operator_guidance_provider_from_env(
                    project_root=workdir,
                    objective=objective,
                    runner=self._backend,
                    model=args.engineer_model,
                    # GROUNDING (Bug 1): see the run's real progress.
                    trace_path=operator_trace_path,
                    checkpoint_path=operator_checkpoint_path,
                    # OBSERVABILITY (Bug 2): emit a marker event per intervention
                    # into the same sink that feeds events.jsonl.
                    on_event=sink.handle_event,
                )
            except Exception:  # noqa: BLE001 — wiring must never break a mission
                sim_provider = None
        # REAL operator inbox (Change A): drain queued ``--notify`` / ``/nudge``
        # messages EACH engineer round — not just at mission start — so the
        # operator can steer a long in-flight mission instead of being locked out
        # until the next mission. Wired through the existing per-round
        # ``extra_guidance_provider`` hook; shares ``inbox.offset`` with the
        # supervisor's mission-start drain, so each message is delivered exactly
        # once with no duplication. Never raises into a mission.
        inbox_life_dir = (
            operator_checkpoint_path.parent
            if operator_checkpoint_path is not None
            else None
        )

        def _combined_guidance_provider() -> list[str]:
            msgs: list[str] = []
            if inbox_life_dir is not None:
                try:
                    from ._inbox import drain_inbox_messages
                    msgs.extend(drain_inbox_messages(inbox_life_dir))
                except Exception:  # noqa: BLE001 — never break a mission
                    pass
            if sim_provider is not None:
                try:
                    msgs.extend(sim_provider() or [])
                except Exception:  # noqa: BLE001
                    pass
            return msgs

        # Preserve the legacy "None when there is nothing to provide" contract
        # (keeps existing tests / chat behaviour unchanged when there is neither
        # an inbox nor the simulated operator).
        extra_guidance_provider = (
            _combined_guidance_provider
            if (sim_provider is not None or inbox_life_dir is not None)
            else None
        )
        loop = self._SkillLoop(
            skills_dir=Path(args.skills_dir),
            engineer_runner=getattr(self, "engineer_backend", None) or self._backend,
            reviewer_runner=getattr(self, "reviewer_backend", None) or self._backend,
            config=config,
            on_event=sink.handle_event,
            extra_guidance_provider=extra_guidance_provider,
            manager=getattr(self, "manager", None),
        )
        full_task = objective
        if prelude_context:
            full_task = f"{prelude_context}\n---\n## Live objective\n{objective}"
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
                original_objective=original_objective or objective,
                scope=mission_scope,
                per_mission_budget=per_mission_budget,
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
        checklist_feedback: dict = {}
        step_back: dict | None = None
        process_lesson: str = ""
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
                process_lesson = str(getattr(_final_review, "process_lesson", "") or "")
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
        stage_transition = self._decide_stage_transition(
            rounds_list=rounds_list, workdir=workdir, sink=sink
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
            checklist_feedback=checklist_feedback,
            step_back=step_back,
            process_lesson=process_lesson,
            stage_transition=stage_transition,
        )

    def _decide_stage_transition(
        self, *, rounds_list: list, workdir: Path, sink: EventSink
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
                project_root=workdir,
                runner=getattr(self, "manager_backend", None) or self._backend,
                skill_store=getattr(self, "_manager_skill_store", None),
            ).decide_stage_transition(
                review=final_review, project_root=workdir,
                on_event=sink.handle_event,
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

    def _simple_match_skill_block(self, objective: str) -> str:
        """Best-effort ONE engineer-skill match for the SIMPLE path (the '+skill'
        lego block). Fail-soft to '' — a simple one-shot runs codex-only when no
        store/skill is available, never erroring on the fast path."""
        try:
            from ..skills.role_match import match_role_skills
            from ..skills.store import SkillStore
            skills_dir = getattr(self._args, "skills_dir", None)
            if not skills_dir:
                return ""
            store = SkillStore(
                Path(skills_dir), runner=self._backend,
                matcher_model=getattr(self._args, "matcher_model", "")
                or self._args.engineer_model,
            )
            match = match_role_skills(store, role="engineer", task=objective,
                                      on_event=self._current_sink and self._current_sink.handle_event)
            return str(getattr(match, "block", "") or "")
        except Exception:  # noqa: BLE001 — skill is an OPTIONAL block
            return ""

    def _simple_quick_reply(
        self,
        *,
        objective: str,
        sink: EventSink,
        seed_thread_id: str | None = None,
    ) -> _Outcome:
        """SIMPLE one-shot: at most ONE skill match + ONE bounded codex turn with
        tools, then done. The lego block between CHAT (no tools) and COMPLEX (full
        pipeline): NO planner, NO iterative reviewer loop, NO skill writeback — the
        operator verifies it. Reached from operator-REPL input (gated by
        ``_allow_chat_fast_path``) and from the narrow bounded status/history
        backlog safety valve; autonomous build/optimization work never lands here.
        """
        from ..core.models import RunnerOptions
        from ..life.router import build_simple_prompt

        args = self._args
        safe_mode = _env_flag("ARGUS_SKILL_SAFE_MODE", False)
        seed = self._next_seed_thread_id if seed_thread_id is None else seed_thread_id

        sink.handle_event({
            "type": "loop.start",
            "text": f"simple: {objective[:80]}",
        })

        self._current_sink = sink
        self._current_failure_ledger = None
        skill_block = self._simple_match_skill_block(objective)
        prompt = build_simple_prompt(objective=objective, skill_block=skill_block)
        workdir = (
            Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        )
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
                run_label="simple-1",
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
            "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
            "cached_input_tokens": int(getattr(result, "cached_input_tokens", 0) or 0),
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
            "text": f"status={status} rounds=1 (simple)",
        })

        return _Outcome(
            success=success,
            status=status,
            stop_reason=stop_reason,
            rounds=1,
            last_thread_id=new_tid,
            chat_mode=False,
            auth_failure=auth_fail,
        )


_TEST_DAEMON_PLANNER_SCRIPT_ENV = "ARGUS_SKILL_DAEMON_TEST_PLANNER_SCRIPT"


def _format_daemon_mode_cell(theme, mem: _SplitMemory) -> str:  # noqa: ANN001
    """Banner ``executor`` cell — the honest one-line daemon state.

    Shows ``life ⚡ daemon: pid X · up Y`` when a 7×24 worker is draining this
    project's backlog, or ``life · no daemon — tasks queue until --daemon`` when
    not. (The old "in-process" wording lied: since the REPL/daemon fusion the
    REPL never executes missions itself — only a daemon drains the backlog.)
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
            + theme.bold_green("⚡ daemon")
            + theme.dim(f": pid {status.pid} · up {uptime} ▸ draining")
        )
        return body
    return (
        f"{theme.bold('life')}    "
        + theme.yellow("no daemon")
        + theme.dim("  — tasks queue until you start one (`argus-skill --daemon`)")
    )


def _codex_preflight_warning() -> str | None:
    """Return a one-line warning if the codex backend cannot run, else None.

    Surfaced on the banner so the user does not discover at mission time
    that ArgusBot or the ``codex`` binary are missing. Best-effort: if
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
        return _SkillLoopRunner(args, seed_thread_id=seed_thread_id)
    raise SystemExit(f"unknown backend: {args.backend}")


# ---------------------------------------------------------------------------
# Supervisor driver (used by both `life run` and chat-mode free text)
# ---------------------------------------------------------------------------

def _repl_check_commands_for_open_ended(
    commands: list[str],
    *,
    open_ended: bool,
    objective: str = "",
) -> list[str]:
    from ..daemon.life_worker import _apply_bounded_to_check_commands

    # WHY M0.7: REPL-launched bounded missions share the same root cause as
    # daemon missions; stage_check must receive --bounded at acceptance time.
    return _apply_bounded_to_check_commands(
        commands,
        bounded=not open_ended,
    )


def _build_repl_supervisor_config(
    *,
    per_mission_cap_usd: float,
    daily_cap_usd: float,
    once: bool,
    max_missions: int,
    project_worktree: Path | None,
    stop_event: threading.Event,
    project_root: Path,
    runtime_context: str,
    continuous: bool,
    continuous_objective: str,
    open_ended: bool,
) -> LifeSupervisorConfig:
    from ..life.telemetry import telemetry_interval_from_env

    return LifeSupervisorConfig(
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
        full_emnlp_gate=open_ended,
        telemetry_dir=project_root,
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
        stderr_sink = LifeStderrSink(quiet=quiet)
        project_root = _memory_project_root(mem)
        sink = JsonlEventSink(stderr_sink, life_dir=project_root)
        # Manager divides the Task first: classify the vertical, split into its
        # Stage template, and COMMIT the choice. The supervisor below then TRUSTS
        # the persisted vertical (life/supervisor/_core.py:2460) and won't
        # re-classify. Fail-open — division must never block a run.
        if continuous and str(continuous_objective).strip():
            try:
                # Prefer the runner's single Manager instance (manager backend);
                # fall back to an ad-hoc Manager only when the runner has none
                # (e.g. the memory runner used in tests).
                mgr = getattr(runner, "manager", None)
                if mgr is None:
                    from ..manager import Manager

                    mgr = Manager(
                        project_root=project_root,
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
                    division = mgr.commit_domain(division.task, division.proposed_domain)
                if not quiet:
                    print(division.headline(), file=sys.stderr)
            except Exception:  # noqa: BLE001 — never block a run on division
                log.debug("manager division skipped", exc_info=True)
        cfg = _build_repl_supervisor_config(
            per_mission_cap_usd=per_mission_cap_usd,
            daily_cap_usd=daily_cap_usd,
            once=once,
            max_missions=max_missions,
            project_worktree=project_worktree,
            stop_event=stop_event,
            project_root=project_root,
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
    quiet: bool = False,
    seed_thread_id: str | None = None,
    continuous: bool = False,
    continuous_objective: str = "",
    open_ended: bool = True,
    allow_chat_fast_path: bool = False,
) -> tuple[dict[str, Any], str | None]:
    ns = argparse.Namespace()
    ns.backend = backend
    from ..tools.capability_vault import resolve_route_model

    ns.engineer_model = os.environ.get("ARGUS_SKILL_ENGINEER_MODEL") or resolve_route_model(
        "engineer"
    )
    reviewer_default = resolve_route_model("reviewer")
    ns.reviewer_model = os.environ.get("ARGUS_SKILL_REVIEWER_MODEL") or reviewer_default
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
    ns.check_commands = _repl_check_commands_for_open_ended(
        list(getattr(ns, "check_commands", []) or []),
        open_ended=open_ended,
        objective=continuous_objective,
    )

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
        f"- Mode: {mode_label}\n"
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


__all__ = [
    "_env_flag",
    "_env_int",
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
    "_repl_check_commands_for_open_ended",
    "_build_repl_supervisor_config",
    "run_life_supervisor",
    "_invoke_supervisor",
]
