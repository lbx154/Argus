"""Deterministic/test runner adapters used by the life runtime."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import RunnerResult
from ..core.ports import EventSink

_TEST_DAEMON_PLANNER_SCRIPT_ENV = "ARGUS_SKILL_DAEMON_TEST_PLANNER_SCRIPT"

@dataclass
class _Outcome:
    """Duck-typed outcome the supervisor reads via ``getattr``."""
    success: bool
    status: str
    stop_reason: str = ""
    stop_kind: str | None = None
    recoverable: bool = False
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
    # Final reviewer's generic research assessment, journaled so Planner/Life
    # cannot declare a targeted research project done without the same evidence.
    research_result: dict = field(default_factory=dict)
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
    # The Manager's stage-transition verdict for this mission completion (the
    # Manager is the sole post-bootstrap writer of current_stage). Shape:
    # ``{"action": advance|hold|rollback, "target_stage", "reason",
    # "current_stage", "source"}``. Empty dict when the decision
    # was skipped (error) or never ran. Journaled by the supervisor; the stage
    # write itself already happened inside execute.
    stage_transition: dict = field(default_factory=dict)
    # The reviewer's ``operator_question`` (reviewer_schema.json) from the
    # FINAL round, when the mission stopped with ``status == "blocked"``. The
    # supervisor persists this onto the backlog item (``pending_question``)
    # so it survives past this one event and /status can list it later —
    # without this, the question only ever existed for as long as whatever
    # cockpit process happened to be tailing events.jsonl at that instant.
    operator_question: str = ""


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
            # Do NOT hardcode the venue — a run configured for AAAI (or any other
            # venue via ARGUS_SKILL_VENUE) was previously written as EMNLP here and
            # then graded/formatted against EMNLP rules. Resolve the venue the same
            # way the rest of the system does (ARGUS_SKILL_VENUE env > default), so
            # the harness records the operator's configured venue rather than
            # asserting one.
            from ..skills.venue_profiles import resolve_venue_profile

            target_venue = resolve_venue_profile(root).key
            state = {
                "current_stage": "plan",
                "mission_type": "research-bootstrap",
                "project": title,
                "objective": objective,
                "target_venue": target_venue,
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
                        "- The system can support a concrete research workflow for the configured venue.",
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
        # Only an explicitly configured research profile may trigger a
        # deterministic scaffold. Other domains own their workspace shape.
        from ..core.bootstrap import (
            inspect_project_bootstrap,
            structured_research_bootstrap_requested,
        )

        root = Path(workdir).expanduser()
        research_requested = structured_research_bootstrap_requested(root)
        preflight = inspect_project_bootstrap(
            root,
            research_requested=research_requested,
        )
        if not preflight.should_bootstrap:
            return
        if research_requested:
            self._materialize_research_bootstrap_seed(objective)

    def execute(
        self,
        *,
        objective: str,
        original_objective: str = "",  # noqa: ARG002 — protocol parity
        sink: EventSink,
        preload_injects: list[str] | None = None,  # noqa: ARG002 — protocol parity
        prelude_context: str = "",  # noqa: ARG002 — protocol parity
        seed_thread_id: str | None = None,  # noqa: ARG002 — protocol parity
        scope: str = "",  # noqa: ARG002 — protocol parity
        preplanned: bool = False,  # noqa: ARG002 — protocol parity
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

__all__ = [
    "_MemoryRunner",
    "_Outcome",
    "_ScriptedPlannerBackend",
    "_TEST_DAEMON_PLANNER_SCRIPT_ENV",
]
