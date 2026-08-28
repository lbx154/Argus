"""A closed Goal Gate task must not permanently shadow the gate reopening.

Caught on a live daemon run, on a clean project, with a real model backend. The
engineering work had succeeded — a 914x speedup, independently verified — but
the campaign could not close:

    life.planner.verdict     Staged project completion held: software
                             final-stage Goal Gate is not Reviewer-certified
    life.planner.task_skipped  duplicate completed task
                               (matched_status=done)
    life.status              planner: all proposed tasks were filtered;
                             retrying after backoff

Five identical verdicts, four skips, an empty backlog, no exit. `done` means the
mission finished, not that the gate closed: a stage-closing, review-required
task can run, get reviewed, and still leave the gate uncertified. The dedupe
filter then treated it as satisfying every later re-proposal of the same gate,
and "a done task carries this signature" is not a condition that changes with
time — the third clause of the deadlock definition in
docs/STATE_MACHINE_AND_DEADLOCKS.md.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from argus_skill.life.memory import Backlog, BacklogItem


class _Filter:
    """The real predicate, on a bare object carrying only what it reads."""

    def __init__(self) -> None:
        from argus_skill.life.supervisor._planning_context import (
            PlanningContextMixin,
        )
        from argus_skill.life.supervisor._planning_cycle_enqueue import (
            PlanningCycleEnqueueMixin,
        )

        self._impl = PlanningCycleEnqueueMixin
        self._item_requires_independent_review = (
            PlanningContextMixin._item_requires_independent_review
        )

    def skipped_as_duplicate(self, *, duplicate: BacklogItem, stage_closing: bool) -> bool:
        """True when the proposal is still filtered out."""
        task = SimpleNamespace(stage_closing=stage_closing)
        escapes = self._impl._gate_reproposal_is_not_a_duplicate(self, task, duplicate)
        return not escapes


def _gate_item(status: str, *, review_required: bool = True) -> BacklogItem:
    tags = ["planner", "scope:bounded", "bounded_dag_node", "stage_closing"]
    if review_required:
        tags.append("review:required")
    else:
        tags.append("review:waived")
    return BacklogItem(
        id="015c2e7b0889",
        ts=time.time(),
        title="Complete and certify the current Goal Gate",
        objective="Goal Gate mission for the active staged project.",
        status=status,
        tags=tags,
    )


def test_a_done_gate_task_does_not_shadow_the_gate_reopening() -> None:
    # The live deadlock, as one assertion.
    assert not _Filter().skipped_as_duplicate(duplicate=_gate_item("done"), stage_closing=True)


@pytest.mark.parametrize("status", ["pending", "running"])
def test_an_in_flight_gate_task_is_still_a_duplicate(status: str) -> None:
    # Anti-spam has to survive the fix: two concurrent copies of the same
    # in-flight certification would be real duplicate work.
    assert _Filter().skipped_as_duplicate(duplicate=_gate_item(status), stage_closing=True)


def test_a_self_reviewed_prior_task_never_satisfies_a_certification() -> None:
    # The pre-existing exemption, unchanged: review semantics are part of task
    # identity, so a self-reviewed task cannot close a gate that demands
    # independent review.
    assert not _Filter().skipped_as_duplicate(
        duplicate=_gate_item("running", review_required=False),
        stage_closing=True,
    )


class _StageCertificationGuard:
    def __init__(self, tmp_path) -> None:
        from argus_skill.life.supervisor._planning_context import PlanningContextMixin
        from argus_skill.life.supervisor._planning_cycle_enqueue import (
            PlanningCycleEnqueueMixin,
        )

        self._impl = PlanningCycleEnqueueMixin
        self._item_pipeline_stage = PlanningCycleEnqueueMixin._item_pipeline_stage
        self._item_is_stage_closing = PlanningContextMixin._item_is_stage_closing
        self.memory = SimpleNamespace(backlog=Backlog(tmp_path / "backlog.jsonl"))

    @staticmethod
    def _current_pipeline_stage() -> str:
        return "baseline"

    def blocker(self):
        return self._impl._stage_closing_reproposal_blocker(
            self,
            SimpleNamespace(stage_closing=True),
        )


def _reviewed_stage_item(
    *,
    item_id: str,
    stage: str = "baseline",
    stage_closing: bool = True,
    finished_ts: float = 10.0,
) -> BacklogItem:
    tags = ["planner", "scope:bounded", f"stage:{stage}"]
    if stage_closing:
        tags.extend(["stage_closing", "review:required"])
    return BacklogItem(
        id=item_id,
        ts=finished_ts - 1,
        title=item_id,
        objective=item_id,
        status="done",
        tags=tags,
        finished_ts=finished_ts,
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "not_certified",
        },
    )


def test_reviewed_gate_requires_an_intervening_repair_before_recertification(
    tmp_path,
) -> None:
    guard = _StageCertificationGuard(tmp_path)
    prior = _reviewed_stage_item(item_id="prior-certification")
    guard.memory.backlog.add(prior)

    blocked = guard.blocker()

    assert blocked is not None
    assert blocked[0].id == prior.id
    assert "non-stage-closing repair" in blocked[1]


def test_host_stage_certificate_blocks_reworded_legacy_reproposal(tmp_path) -> None:
    from argus_skill.core.stage_certificate import record_stage_review

    guard = _StageCertificationGuard(tmp_path)
    legacy = SimpleNamespace(
        id="legacy-certification",
        acceptance_check="review baseline",
        context_refs=[],
    )
    guard.memory.root = tmp_path
    record_stage_review(
        state_root=guard.memory.root,
        project_root=tmp_path,
        stage="baseline",
        item=legacy,
        manager_action="hold",
    )

    blocked = guard.blocker()

    assert blocked is not None
    assert blocked[0].id == "legacy-certification"


def test_successful_same_stage_repair_unlocks_one_recertification(tmp_path) -> None:
    guard = _StageCertificationGuard(tmp_path)
    guard.memory.backlog.add(_reviewed_stage_item(item_id="prior-certification"))
    guard.memory.backlog.add(
        _reviewed_stage_item(
            item_id="evidence-repair",
            stage_closing=False,
            finished_ts=20.0,
        )
    )

    assert guard.blocker() is None


def test_reviewed_gate_from_another_stage_does_not_block_current_stage(tmp_path) -> None:
    guard = _StageCertificationGuard(tmp_path)
    guard.memory.backlog.add(
        _reviewed_stage_item(item_id="scope-certification", stage="scope")
    )

    assert guard.blocker() is None


def test_planner_tasks_are_tagged_with_the_current_stage() -> None:
    from argus_skill.life.supervisor._planning_context import PlanningContextMixin

    class Harness(PlanningContextMixin):
        @staticmethod
        def _normalize_planner_scope(_value: str) -> str:
            return "bounded"

        @staticmethod
        def _effective_final_certification_gate(_root) -> bool:
            return False

        @staticmethod
        def _artifact_root():
            return "."

        @staticmethod
        def _current_pipeline_stage() -> str:
            return "baseline"

    tags = Harness()._planner_task_tags(
        SimpleNamespace(
            scope="bounded",
            stage_closing=True,
            require_independent_review=True,
            skip_stage_transition=False,
        )
    )

    assert "stage:baseline" in tags


def test_terminal_unrecoverable_gate_is_indexed_as_duplicate(tmp_path) -> None:
    from argus_skill.life.supervisor._helpers import _planner_task_signature
    from argus_skill.life.supervisor._planning_cycle_enqueue import (
        PlanningCycleEnqueueMixin,
    )
    from argus_skill.life.supervisor._planning_cycle_helpers import _PlanCycleState

    blocked = _gate_item("failed")
    blocked.outcome = {
        "execution_status": "blocked",
        "review_status": "blocked",
        "resumable": False,
    }

    class Harness(PlanningCycleEnqueueMixin):
        def __init__(self) -> None:
            backlog = Backlog(tmp_path / "backlog.jsonl")
            backlog.add(blocked)
            self.memory = SimpleNamespace(backlog=backlog)

        def _planner_scope_from_item(self, _item) -> str:
            return "bounded"

        def _item_is_stage_closing(self, item) -> bool:
            return "stage_closing" in item.tags

        def _item_requires_independent_review(self, item) -> bool:
            return "review:required" in item.tags

        def _item_skips_stage_transition(self, _item) -> bool:
            return False

        def _recent_no_progress_failures(self) -> dict:
            return {}

    state = _PlanCycleState(None)
    harness = Harness()
    harness._pc_build_dedupe_index(state)
    signature = _planner_task_signature(
        blocked.title,
        blocked.objective,
        scope="bounded",
        stage_closing=True,
        require_independent_review=True,
    )

    assert state.seen_signatures[signature].id == blocked.id
    assert state.terminal_blocker_fingerprints[f"item:{blocked.id}"].id == blocked.id
    assert not harness._gate_reproposal_is_not_a_duplicate(
        SimpleNamespace(stage_closing=True),
        blocked,
    )


def test_reworded_terminal_blocker_is_filtered_by_stable_fingerprint(
    tmp_path,
) -> None:
    from argus_skill.life.supervisor._planning_cycle_enqueue import (
        PlanningCycleEnqueueMixin,
    )
    from argus_skill.life.supervisor._planning_cycle_helpers import _PlanCycleState
    from argus_skill.planner import PlannerVerdict, TaskSpec

    blocked = _gate_item("failed", review_required=False)
    blocked.outcome = {
        "execution_status": "blocked",
        "review_status": "blocked",
        "resumable": False,
    }

    class Harness(PlanningCycleEnqueueMixin):
        def __init__(self) -> None:
            backlog = Backlog(tmp_path / "backlog.jsonl")
            backlog.add(blocked)
            self.memory = SimpleNamespace(backlog=backlog)
            self.config = SimpleNamespace(continuous_objective="finish project")
            self._planning_cycles = 1
            self.events: list[dict] = []

        def _emit(self, event: dict) -> bool:
            self.events.append(event)
            return True

        def _emit_status(self, _text: str) -> None:
            return None

        def _project_workdir(self):
            return tmp_path

        def _normalize_planner_scope(self, value: str) -> str:
            return value or "bounded"

        def _planner_scope_from_item(self, _item) -> str:
            return "bounded"

        def _item_is_stage_closing(self, item) -> bool:
            return "stage_closing" in item.tags

        def _item_requires_independent_review(self, item) -> bool:
            return "review:required" in item.tags

        def _item_skips_stage_transition(self, _item) -> bool:
            return False

        def _recent_no_progress_failures(self) -> dict:
            return {}

        def _task_mentions_family(self, _task, _family: str) -> bool:
            return False

        def _validated_task_authorization(self, _task) -> tuple[str, str]:
            return "", ""

        def _planner_task_tags(self, _task) -> list[str]:
            return ["planner", "scope:bounded", "stage_closing", "review:required"]

        def _item_iteration_cycles(self) -> int:
            return 1

    state = _PlanCycleState(None)
    state.verdict = PlannerVerdict(
        project_done=False,
        reason="try the same impossible boundary again",
        new_tasks=[
            TaskSpec(
                title="A completely different title",
                objective="Rephrased instructions that still target the same boundary.",
                evidence="Reworded evidence.",
                blocker_fingerprint=f"  ITEM:{blocked.id.upper()}  ",
                stage_closing=True,
            )
        ],
    )
    harness = Harness()

    harness._pc_build_dedupe_index(state)
    harness._pc_build_pending_items(state)

    assert state.pending_items == []
    assert harness.events[-1]["matched_item_id"] == blocked.id
    assert harness.events[-1]["reason"] == "duplicate terminal blocker"


def test_terminal_fingerprint_takes_priority_over_completed_signature(
    tmp_path,
) -> None:
    from argus_skill.life.supervisor._planning_cycle_enqueue import (
        PlanningCycleEnqueueMixin,
    )
    from argus_skill.life.supervisor._planning_cycle_helpers import _PlanCycleState
    from argus_skill.planner import PlannerVerdict, TaskSpec

    blocked = _gate_item("failed", review_required=False)
    blocked.outcome = {
        "execution_status": "blocked",
        "review_status": "blocked",
        "resumable": False,
    }
    completed = _gate_item("done")
    completed.id = "completed-copy"
    completed.title = "Reused title"
    completed.objective = "Reused objective"

    class Harness(PlanningCycleEnqueueMixin):
        def __init__(self) -> None:
            backlog = Backlog(tmp_path / "backlog.jsonl")
            backlog.add(blocked)
            backlog.add(completed)
            self.memory = SimpleNamespace(backlog=backlog)
            self.config = SimpleNamespace(continuous_objective="finish project")
            self._planning_cycles = 1
            self.events: list[dict] = []

        def _emit(self, event: dict) -> bool:
            self.events.append(event)
            return True

        def _emit_status(self, _text: str) -> None:
            return None

        def _project_workdir(self):
            return tmp_path

        def _normalize_planner_scope(self, value: str) -> str:
            return value or "bounded"

        def _planner_scope_from_item(self, _item) -> str:
            return "bounded"

        def _item_is_stage_closing(self, item) -> bool:
            return "stage_closing" in item.tags

        def _item_requires_independent_review(self, item) -> bool:
            return "review:required" in item.tags

        def _item_skips_stage_transition(self, _item) -> bool:
            return False

        def _recent_no_progress_failures(self) -> dict:
            return {}

        def _task_mentions_family(self, _task, _family: str) -> bool:
            return False

        def _validated_task_authorization(self, _task) -> tuple[str, str]:
            return "", ""

        def _planner_task_tags(self, _task) -> list[str]:
            return ["planner", "scope:bounded", "stage_closing", "review:required"]

        def _item_iteration_cycles(self) -> int:
            return 1

    state = _PlanCycleState(None)
    state.verdict = PlannerVerdict(
        project_done=False,
        reason="retry blocked work under completed prose",
        new_tasks=[
            TaskSpec(
                title=completed.title,
                objective=completed.objective,
                blocker_fingerprint=f"item:{blocked.id}",
                stage_closing=True,
            )
        ],
    )
    harness = Harness()

    harness._pc_build_dedupe_index(state)
    harness._pc_build_pending_items(state)

    assert state.pending_items == []
    assert harness.events[-1]["matched_item_id"] == blocked.id
    assert harness.events[-1]["reason"] == "duplicate terminal blocker"


@pytest.mark.parametrize("status", ["done", "pending", "running"])
def test_ordinary_tasks_are_unaffected(status: str) -> None:
    # Only stage-closing proposals get the exemption; ordinary work keeps the
    # plain dedupe, or the Planner could re-enqueue finished work forever.
    assert _Filter().skipped_as_duplicate(duplicate=_gate_item(status), stage_closing=False)
