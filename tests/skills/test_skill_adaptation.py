from __future__ import annotations

from pathlib import Path

from argus_skill.core.models import RunnerResult
from argus_skill.skills.adaptation import (
    adaptation_state_path,
    append_method_ledger,
    load_adaptation_state,
    save_adaptation_state,
)
from argus_skill.skills.missions import EngineerMission
from argus_skill.skills.scientist import (
    SkillScientist,
    parse_mechanism_change,
)
from argus_skill.skills.skill_router import SkillRouter
from argus_skill.skills.store import SkillStore


class _ScientistBackend:
    def run_exec(self, **_kwargs) -> RunnerResult:
        return RunnerResult(
            exit_code=0,
            agent_messages=[
                "# Alternative proof strategy\n"
                "## Description\nReusable structural decomposition.\n"
                "## Category\nmath-research\n"
                "## When to use\n- A direct route failed.\n"
                "## When NOT to use\n- The decomposition assumptions fail.\n"
                "## Mechanism change\n"
                "Previous mechanism: direct coefficient search\n"
                "Replacement mechanism: invariant-preserving decomposition\n"
                "Structural difference: the replacement proves closure properties "
                "instead of tuning the original search bounds.\n"
                "## How to solve\n1. Identify the invariant.\n"
                "## Pitfalls\n- Recheck every hypothesis.\n"
            ],
        )


class _TimedOutScientistBackend:
    def run_exec(self, **_kwargs) -> RunnerResult:
        return RunnerResult(
            exit_code=-15,
            agent_messages=["# Partial unreviewed skill"],
            fatal_error=(
                "External interrupt: scientist skill distill time budget reached "
                "after 120s; yield for review/steering"
            ),
        )


def test_timed_out_scientist_does_not_activate_partial_skill() -> None:
    scientist = SkillScientist(_TimedOutScientistBackend(), model="test")

    assert scientist.distill_alternative("solve a conjecture", "reviewer said no") == ""


def test_adaptation_state_is_generic_restart_safe_project_state(tmp_path: Path) -> None:
    checkpoint = tmp_path / "life" / "checkpoint.json"
    path = adaptation_state_path(checkpoint, "mission-1")

    save_adaptation_state(
        path,
        "mission-1",
        trigger_count=1,
        spent_usd=0.75,
        rejection_streak=[
            {"round_index": 2, "reason": "method failed", "next_action": "replace it"}
        ],
        method_records=[{"status": "created", "trigger_index": 1}],
    )

    assert "skill_adaptation" in path.parts
    assert load_adaptation_state(path, "mission-1") == {
        "trigger_count": 1,
        "spent_usd": 0.75,
        "rejection_streak": [
            {"round_index": 2, "reason": "method failed", "next_action": "replace it"}
        ],
        "method_records": [{"status": "created", "trigger_index": 1}],
    }


def test_method_ledger_is_not_owned_by_math_vertical(tmp_path: Path) -> None:
    path = append_method_ledger(
        tmp_path,
        {"status": "method_failure", "trigger_index": 1},
    )

    assert path == tmp_path / "research" / "METHOD_LEDGER.jsonl"
    assert "verticals/math" not in path.as_posix()


def test_scientist_alternative_enters_generic_versioned_skill_store(
    tmp_path: Path,
) -> None:
    store = SkillStore(tmp_path / "skills")
    router = SkillRouter(
        skill_store=store,
        matcher=EngineerMission(store),
    )
    scientist = SkillScientist(_ScientistBackend(), model="test")

    markdown = scientist.distill_alternative(
        "prove a conjecture",
        "Reviewer: method_failure",
        current_skill="direct coefficient search",
    )
    mechanism = parse_mechanism_change(markdown)
    created = router.create_from_scientist(
        markdown,
        task="prove a conjecture",
    )

    assert mechanism is not None
    assert created is not None
    assert created.path is not None and Path(created.path).exists()



def test_scientist_call_does_not_impose_a_time_limit() -> None:
    class RecordingBackend:
        def __init__(self) -> None:
            self.kwargs = None

        def run_exec(self, **kwargs):
            self.kwargs = kwargs
            return RunnerResult(exit_code=0, agent_messages=["NONE"])

    recording = RecordingBackend()
    SkillScientist(recording, model="test").distill_alternative(
        "scope a problem", "reviewer said no"
    )
    assert recording.kwargs is not None
    assert recording.kwargs["options"].watchdog_hard_idle_seconds is None
