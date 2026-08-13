"""Deterministic/test runner adapters used by the life runtime."""

from __future__ import annotations

import json
import os
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
    # Set only when a final-submission mission receives Reviewer ``done``.
    final_submission_certified: bool = False
    completion_evidence: str = ""
    # The Manager's stage-transition verdict for this mission completion (the
    # Manager is the sole writer of current_stage). Shape:
    # ``{"action": advance|hold|rollback|complete, "target_stage", "reason",
    # "current_stage", "source"}``. Empty dict when the decision
    # was skipped (error) or never ran. Journaled by the supervisor; the stage
    # write itself already happened inside execute.
    stage_transition: dict = field(default_factory=dict)
    # True when a trusted review-only workflow deliberately bypassed the formal
    # stage writer. Persisted separately so recovery cannot replay the review.
    stage_transition_skipped: bool = False
    # The reviewer's named ``OPERATOR_QUESTION`` verdict field from the
    # FINAL round, when the mission stopped with ``status == "blocked"``. The
    # supervisor persists this onto the backlog item (``pending_question``)
    # so it survives past this one event and /status can list it later —
    # without this, the question only ever existed for as long as whatever
    # cockpit process happened to be tailing events.jsonl at that instant.
    operator_question: str = ""
    operator_options: list[dict] = field(default_factory=list)
    final_review_status: str = ""
    final_review_source: str = ""
    final_review_reason: str = ""
    final_review_next_action: str = ""
    summary: str = ""
    # Full operator-facing Engineer handoff. ``summary`` stays compact for
    # status surfaces, while this field preserves long-form deliverables.
    final_output: str = ""
    research_result: dict | None = None
    final_planner_report: dict = field(default_factory=dict)
    plan_challenge: dict = field(default_factory=dict)


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
        self.manager: Any = None

    def execute(
        self,
        *,
        objective: str,
        original_objective: str = "",  # noqa: ARG002 — protocol parity
        review_objective: str = "",  # noqa: ARG002 — protocol parity
        sink: EventSink,
        preload_injects: list[str] | None = None,  # noqa: ARG002 — protocol parity
        prelude_context: str = "",  # noqa: ARG002 — protocol parity
        seed_thread_id: str | None = None,  # noqa: ARG002 — protocol parity
        scope: str = "",  # noqa: ARG002 — protocol parity
        preplanned: bool = False,  # noqa: ARG002 — protocol parity
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
