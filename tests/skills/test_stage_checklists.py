from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from argus.core.pipeline_state import read_pipeline_state
from argus.reviewer._core import (
    ReviewerConfig,
    _parallel_final_review_passes,
)
from argus.skills.vertical_select import persist_vertical
from argus.verticals.research.stages import (
    CANONICAL_STAGE_ORDER,
    STAGE_CHECKLISTS,
    stage_completion_issues,
)


def test_research_has_exactly_four_forward_stages() -> None:
    assert CANONICAL_STAGE_ORDER == (
        "idea",
        "experiment",
        "paper",
        "review",
    )
    assert tuple(STAGE_CHECKLISTS) == CANONICAL_STAGE_ORDER


def test_paper_completion_issues_do_not_mention_the_notes_file(
    tmp_path: Path,
) -> None:
    issues = stage_completion_issues("paper", tmp_path)
    assert issues
    assert not any("HANDOFF" in issue or "RESEARCH_NOTES" in issue for issue in issues)


def test_initial_final_review_runs_three_passes_in_parallel_with_engineer_access(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    persist_vertical(state, "research")
    path = state / ".argus" / "PIPELINE_STATE.json"
    payload = read_pipeline_state(state)
    payload["current_stage"] = "review"
    path.write_text(json.dumps(payload), encoding="utf-8")
    stale = workdir / "paper" / "REVIEW.md"
    stale.parent.mkdir()
    stale.write_text("stale prior review", encoding="utf-8")

    class Runner:
        def __init__(self, shared=None) -> None:
            self.shared = shared or SimpleNamespace(
                barrier=threading.Barrier(3),
                active=0,
                max_active=0,
                options=[],
                lock=threading.Lock(),
            )

        def fork(self):
            return Runner(self.shared)

        def run_exec(self, **kwargs):
            with self.shared.lock:
                self.shared.active += 1
                self.shared.max_active = max(
                    self.shared.max_active,
                    self.shared.active,
                )
                self.shared.options.append(kwargs["options"])
            self.shared.barrier.wait(timeout=2)
            with self.shared.lock:
                self.shared.active -= 1
            return SimpleNamespace(
                exit_code=0,
                fatal_error=None,
                agent_messages=[kwargs["run_label"]],
            )

    runner = Runner()
    decision = _parallel_final_review_passes(
        runner,
        ReviewerConfig(
            active_vertical="research",
            working_dir=str(workdir),
            artifact_root=str(workdir),
            vertical_state_root=str(state),
            full_auto=True,
        ),
    )

    assert decision is not None
    assert decision.status == "continue"
    assert all(label in decision.reason for label in ("Scientific:", "Visual:", "Language:"))
    assert runner.shared.max_active == 3
    assert all(
        option.full_auto and option.sandbox_mode is None and not option.force_safe_mode
        for option in runner.shared.options
    )


def test_parallel_review_prefers_returned_control_stop_over_generic_exception(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    persist_vertical(state, "research")
    path = state / ".argus" / "PIPELINE_STATE.json"
    payload = read_pipeline_state(state)
    payload["current_stage"] = "review"
    path.write_text(json.dumps(payload), encoding="utf-8")

    class Runner:
        def fork(self):
            return Runner()

        def run_exec(self, **kwargs):
            label = kwargs["run_label"]
            if label == "reviewer-visual":
                raise RuntimeError("transport failed")
            aborted = label == "reviewer-scientific"
            return SimpleNamespace(
                exit_code=1 if aborted else 0,
                fatal_error="operator stopped review" if aborted else None,
                stop_kind="operator_abort" if aborted else None,
                agent_messages=[] if aborted else ["pass"],
                input_tokens=2,
                cached_input_tokens=0,
                output_tokens=1,
                reasoning_output_tokens=0,
                premium_requests=0.0,
            )

    decision = _parallel_final_review_passes(
        Runner(),
        ReviewerConfig(
            active_vertical="research",
            working_dir=str(workdir),
            artifact_root=str(workdir),
            vertical_state_root=str(state),
        ),
    )

    assert decision is not None
    assert decision.status == "blocked"
    assert decision.backend_stop_kind == "operator_abort"
    assert decision.backend_fatal_error == "operator stopped review"
    assert decision.input_tokens == 4
    assert decision.output_tokens == 2
