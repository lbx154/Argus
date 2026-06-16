from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from ...core.models import RunnerResult
from ...core.ports import EventSink
from ...engineer.runner import should_clear_thread_id_after_outcome
from . import _core
from ._base import (
    _checkpoint_path_for,
    _env_flag,
    _env_int,
    _Outcome,
)


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
        from ...core.bootstrap import inspect_project_bootstrap
        from ...life.research_profile import load_research_profile

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


class _ScriptedPlannerBackend:
    """Test-only planner backend for daemon continuous-mode integration."""

    def __init__(self, *, planner: list[dict[str, Any]], critic: list[dict[str, Any]]) -> None:
        self._planner = list(planner)
        self._critic = list(critic)

    @classmethod
    def from_env(cls) -> "_ScriptedPlannerBackend | None":
        raw_path = os.environ.get(_core._TEST_DAEMON_PLANNER_SCRIPT_ENV, "").strip()
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
        from ...loop import SkillLoop, SkillLoopConfig

        self._SkillLoop = SkillLoop
        self._SkillLoopConfig = SkillLoopConfig
        try:
            from ...adapters.codex_backend import CodexRunnerBackend
            from ...adapters.stream_progress import make_stream_progress_callback
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
        from ...adapters.codex_backend import _strip_legacy_codex_profile_args
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
            from ...core.models import RunnerOptions
            from ...life.router import classify_is_conversational

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
        workdir = (
            Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        )
        # Opt-in LONG-TAILED SIMULATED HUMAN OPERATOR (default OFF). When
        # ARGUS_SKILL_SIMULATED_OPERATOR=1 the env-gated helper returns a
        # provider that injects one grounded, long-tailed operator message per
        # engineer round (rendered by the loop as "## Operator guidance"). With
        # the flag OFF it returns None and the loop is built exactly as before,
        # so existing behaviour/tests are unchanged. Never raises into a mission.
        extra_guidance_provider = None
        try:
            from ...life.operator_sim import operator_guidance_provider_from_env

            # GROUNDING (Bug 1): the run's real telemetry does NOT live in the
            # git work-tree. The daemon/REPL fan events out to
            # ``<life_dir>/events.jsonl`` in the per-project state dir, right
            # next to the per-round reviewer ``checkpoint.json``. So derive the
            # trace path from the checkpoint's parent dir; only fall back to a
            # work-tree-local events.jsonl when checkpoint persistence is off.
            operator_checkpoint_path = _checkpoint_path_for(args, workdir)
            if operator_checkpoint_path is not None:
                operator_trace_path = operator_checkpoint_path.parent / "events.jsonl"
            else:
                operator_trace_path = workdir / "events.jsonl"

            extra_guidance_provider = operator_guidance_provider_from_env(
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
            extra_guidance_provider = None
        loop = self._SkillLoop(
            skills_dir=Path(args.skills_dir),
            engineer_runner=self._backend,
            reviewer_runner=self._backend,
            config=config,
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
        from ...engineer.failed_tool_ledger import FailedToolLedger
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
        from ...core.models import RunnerOptions

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
        from ...core.models import RunnerOptions
        from ...life.router import build_chat_prompt

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

