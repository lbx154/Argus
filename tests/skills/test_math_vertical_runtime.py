from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill import SkillLoop, SkillLoopConfig, SkillStore
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.core.models import RunnerOptions, RunnerResult
from argus_skill.manager.plan_mode import build_plan_prompt
from argus_skill.planner.planner import Planner
from argus_skill.reviewer import Reviewer, ReviewerConfig
from argus_skill.skills.scientist import (
    _build_scientist_prompt,
    parse_mechanism_change,
)
from argus_skill.skills.stage_checklists import advance_stage
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals.math.runtime import (
    load_math_adaptation_state,
    math_adaptation_state_path,
    math_role_banner,
)

BASE_SKILL = """\
# Algebraic normalization
## Description
Normalize one equation and search a fixed parameter family.
## Category
mathematics
## When to use
- Use for a Diophantine equation with a fixed-parameter route.
## When NOT to use
- Do not use after the fixed family has exact counterexamples.
## How to solve
1. Normalize the equation.
2. Search the fixed family.
## Pitfalls
- Do not extrapolate finite checks.
"""

ALTERNATIVE_SKILL = """\
# Structural descent strategy
## Description
Use a descent invariant instead of the failed fixed-parameter search.
## Category
mathematics
## When to use
- Use after exact counterexamples kill a fixed family.
## When NOT to use
- Do not use without a well-founded measure.
## Mechanism change
Previous mechanism: fixed-parameter enumeration
Replacement mechanism: well-founded structural descent
Structural difference: replaces bounded enumeration with a proof-preserving decreasing transformation.
## How to solve
1. Define a well-founded measure.
2. Prove every nonterminal object has a decreasing transformation.
## Pitfalls
- Reject transformations that do not preserve every hypothesis.
"""


def _seed_skill(skills_dir: Path) -> None:
    SkillStore(skills_dir).save_distilled(
        task_description="prove the divisibility theorem",
        raw_distill_output=BASE_SKILL,
    )


def _match() -> CannedResponse:
    return CannedResponse(
        message=json.dumps(
            {
                "matched": [
                    {
                        "name": "Algebraic normalization",
                        "fit": "high",
                        "why": "mathematical proof task",
                    }
                ]
            }
        )
    )


def _continue(
    round_index: int,
    *,
    failure_cause: str = "method_failure",
) -> CannedResponse:
    return CannedResponse(
        message=json.dumps(
            {
                "status": "continue",
                "reason": f"round {round_index} reused the failed fixed mechanism",
                "next_action": "Use a structurally different proof mechanism.",
                "failure_cause": failure_cause,
                "round_summary_markdown": f"# Round {round_index}\n\n- rejected\n",
                "completion_summary_markdown": "",
            }
        )
    )


def _done_math() -> CannedResponse:
    return CannedResponse(
        message=json.dumps(
            {
                "status": "done",
                "reason": "The known divisibility result is correctly proved.",
                "next_action": "No further action.",
                "round_summary_markdown": "# Review\n\n- proof verified\n",
                "completion_summary_markdown": "Done.",
                "math_result": {
                    "result_class": "known_result",
                    "correctness": "verified",
                    "novelty": "known",
                    "statement_fidelity": "verified",
                    "evidence": ["checked exact divisibility proof"],
                    "limitations": ["not a novel theorem"],
                },
            }
        )
    )


def _blocked_math() -> CannedResponse:
    payload = json.loads(_done_math().message)
    payload.update({
        "status": "blocked",
        "reason": "An operator decision is required.",
        "next_action": "Resume the same mission after the decision.",
        "completion_summary_markdown": "",
    })
    return CannedResponse(message=json.dumps(payload))


class _CostedMemoryBackend(MemoryBackend):
    def __init__(self, scientist_cost_usd: float) -> None:
        super().__init__()
        self.scientist_cost_usd = scientist_cost_usd

    def run_exec(
        self,
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        result = super().run_exec(
            prompt=prompt,
            options=options,
            run_label=run_label,
            resume_thread_id=resume_thread_id,
        )
        if run_label == "scientist.skill_distill":
            result.cost_usd = self.scientist_cost_usd
        return result


class _CrashOnScientistBackend(MemoryBackend):
    def run_exec(
        self,
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        if run_label == "scientist.skill_distill":
            self.history.append((run_label, prompt, options))
            self.resume_history.append((run_label, resume_thread_id))
            raise KeyboardInterrupt("simulated process crash after provider spawn")
        return super().run_exec(
            prompt=prompt,
            options=options,
            run_label=run_label,
            resume_thread_id=resume_thread_id,
        )


def test_math_rejections_trigger_bounded_scientist_and_inject_next_round(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "math")
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)
    backend = MemoryBackend()
    backend.queue("matcher", _match())
    for index in range(1, 5):
        backend.queue(
            f"engineer-r{index}",
            CannedResponse(message=f"attempt {index}"),
        )
        backend.queue(
            "reviewer",
            _continue(
                index,
                failure_cause="skill_gap" if index == 1 else "method_failure",
            ),
        )
    backend.queue(
        "scientist.skill_distill",
        CannedResponse(message=ALTERNATIVE_SKILL),
    )
    backend.queue("engineer-r5", CannedResponse(message="descent proof complete"))
    backend.queue("reviewer", _done_math())
    events: list[dict] = []
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=5,
            math_adaptive_rejection_threshold=2,
            math_adaptive_skill_max_triggers=1,
            math_adaptive_skill_max_cost_usd=1.5,
        ),
        on_event=events.append,
    )

    outcome = loop.run("prove the divisibility theorem", workdir=tmp_path)

    assert outcome.successful
    scientist_calls = [
        (prompt, options)
        for label, prompt, options in backend.history
        if label == "scientist.skill_distill"
    ]
    assert len(scientist_calls) == 1
    scientist_prompt, scientist_options = scientist_calls[0]
    assert "MISSION TYPE: MATHEMATICS" in scientist_prompt
    assert "failed mechanism" in scientist_prompt
    assert "round 1 reused the failed fixed mechanism" in scientist_prompt
    assert "round 2 reused the failed fixed mechanism" in scientist_prompt
    assert "Changing only constants" in scientist_prompt
    assert scientist_options.max_budget_usd == 1.5
    engineer_first = next(
        prompt for label, prompt, _ in backend.history if label == "engineer-r1"
    )
    engineer_after_adaptation = next(
        prompt for label, prompt, _ in backend.history if label == "engineer-r3"
    )
    assert "MISSION TYPE: MATHEMATICS" in engineer_first
    assert "Structural descent strategy" in engineer_after_adaptation
    ledger = tmp_path / "research" / "MATH_METHOD_LEDGER.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["status"] == "created"
    assert rows[0]["review_rounds"] == [1, 2]
    assert rows[0]["mechanism_change_required"] is True
    assert any(
        event.get("type") == "skill.scientist.adaptation_created"
        for event in events
    )


def test_non_math_prompts_remain_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    assert math_role_banner(tmp_path, "engineer") == ""
    assert math_role_banner(tmp_path, "scientist") == ""
    prompt = SkillLoop._build_engineer_prompt(
        task="say hello",
        skill_text="",
        next_action=None,
    )
    scientist_prompt = _build_scientist_prompt("say hello")

    assert "MISSION TYPE: MATHEMATICS" not in prompt
    assert "MISSION TYPE: MATHEMATICS" not in scientist_prompt


def test_mechanism_change_requires_distinct_structured_declaration() -> None:
    assert parse_mechanism_change(ALTERNATIVE_SKILL) is not None
    same = ALTERNATIVE_SKILL.replace(
        "Replacement mechanism: well-founded structural descent",
        "Replacement mechanism: fixed-parameter enumeration",
    )
    assert parse_mechanism_change(same) is None
    assert parse_mechanism_change(BASE_SKILL) is None


def test_math_planner_prompt_receives_role_banner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    persist_vertical(tmp_path, "math")
    monkeypatch.chdir(tmp_path)

    prompt = Planner._build_planner_prompt(
        continuous_objective="prove or refute the theorem",
        journal_tail="",
        planning_cycle=0,
    )

    assert "MISSION TYPE: MATHEMATICS" in prompt
    assert "structured lean_check tool" in prompt
    assert "novelty checks" in prompt


def test_bounded_math_planner_prompt_receives_role_banner(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "math")
    banner = math_role_banner(tmp_path, "planner")

    prompt = build_plan_prompt(
        "prove or refute the theorem",
        role_banner=banner,
    )

    assert "MISSION TYPE: MATHEMATICS" in prompt
    assert "structured lean_check tool" in prompt


def test_bounded_non_math_plan_prompt_is_byte_identical() -> None:
    objective = "ordinary bounded task"

    assert build_plan_prompt(objective, role_banner="") == build_plan_prompt(
        objective
    )


def test_math_reviewer_holds_unverified_novelty(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "math")
    advance_stage(tmp_path, target_stage="solve", reason="test")
    advance_stage(tmp_path, target_stage="review", reason="test")
    backend = MemoryBackend()
    payload = json.loads(_done_math().message)
    payload["math_result"]["result_class"] = "novelty_unverified"
    payload["math_result"]["novelty"] = "unverified"
    backend.queue("reviewer", CannedResponse(message=json.dumps(payload)))

    decision = Reviewer(backend).evaluate(
        objective="prove a new theorem",
        round_index=1,
        session_id=None,
        main_summary="candidate proof",
        main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )

    assert decision.status == "continue"
    assert "math_novelty_not_verified" in decision.reason
    review_prompt = next(
        prompt for label, prompt, _ in backend.history if label == "reviewer"
    )
    review_options = next(
        options for label, _, options in backend.history if label == "reviewer"
    )
    assert "MISSION TYPE: MATHEMATICS" in review_prompt
    assert "math_result" in review_prompt
    assert review_options.output_schema_path.endswith("reviewer_math_schema.json")
    assert decision.to_event_payload()["math_result"]["novelty"] == "unverified"


def test_math_reviewer_fails_closed_when_completion_policy_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persist_vertical(tmp_path, "math")
    advance_stage(tmp_path, target_stage="solve", reason="test")
    advance_stage(tmp_path, target_stage="review", reason="test")
    backend = MemoryBackend()
    backend.queue("reviewer", _done_math())

    def broken_policy(*args, **kwargs):
        raise RuntimeError("simulated policy failure")

    monkeypatch.setattr(
        "argus_skill.verticals.math.results.math_completion_issue",
        broken_policy,
    )
    decision = Reviewer(backend).evaluate(
        objective="prove the theorem",
        round_index=1,
        session_id=None,
        main_summary="candidate proof",
        main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )

    assert decision.status == "continue"
    assert "completion policy evaluation failed" in decision.reason


def test_math_custom_schema_still_fails_closed_on_policy_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persist_vertical(tmp_path, "math")
    backend = MemoryBackend()
    backend.queue("reviewer", _done_math())
    custom_schema = tmp_path / "custom-reviewer-schema.json"
    custom_schema.write_text("{}\n", encoding="utf-8")
    reviewer = Reviewer(backend)
    reviewer.schema_path = str(custom_schema)

    def broken_policy(*args, **kwargs):
        raise RuntimeError("simulated policy failure")

    monkeypatch.setattr(
        "argus_skill.verticals.math.results.math_completion_issue",
        broken_policy,
    )
    decision = reviewer.evaluate(
        objective="prove the theorem",
        round_index=1,
        session_id=None,
        main_summary="candidate proof",
        main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )

    assert decision.status == "continue"
    assert "completion policy evaluation failed" in decision.reason


def test_math_bounded_solve_result_can_finish_without_novelty(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "math")
    advance_stage(tmp_path, target_stage="solve", reason="test")
    backend = MemoryBackend()
    payload = json.loads(_done_math().message)
    payload["math_result"]["result_class"] = "finite_verification"
    payload["math_result"]["novelty"] = "not_applicable"
    backend.queue("reviewer", CannedResponse(message=json.dumps(payload)))

    decision = Reviewer(backend).evaluate(
        objective="verify the first 100 cases",
        round_index=1,
        session_id=None,
        main_summary="finite verification complete",
        main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )

    assert decision.status == "done"


def test_math_bounded_review_result_can_finish_without_novelty(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "math")
    advance_stage(tmp_path, target_stage="solve", reason="test")
    advance_stage(tmp_path, target_stage="review", reason="test")
    backend = MemoryBackend()
    payload = json.loads(_done_math().message)
    payload["math_result"]["result_class"] = "finite_verification"
    payload["math_result"]["novelty"] = "not_applicable"
    backend.queue("reviewer", CannedResponse(message=json.dumps(payload)))

    decision = Reviewer(backend).evaluate(
        objective="verify the first 100 cases",
        round_index=1,
        session_id=None,
        main_summary="finite verification complete",
        main_error=None,
        scope="bounded",
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )

    assert decision.status == "done"


@pytest.mark.parametrize(
    ("field", "value", "issue"),
    [
        ("correctness", "incorrect", "math_correctness_not_verified"),
        ("statement_fidelity", "failed", "statement_fidelity_not_verified"),
        ("evidence", [], "missing_math_evidence"),
    ],
)
def test_math_bounded_result_still_requires_valid_evidence(
    tmp_path: Path,
    field: str,
    value: object,
    issue: str,
) -> None:
    persist_vertical(tmp_path, "math")
    advance_stage(tmp_path, target_stage="solve", reason="test")
    advance_stage(tmp_path, target_stage="review", reason="test")
    backend = MemoryBackend()
    payload = json.loads(_done_math().message)
    payload["math_result"]["result_class"] = "finite_verification"
    payload["math_result"]["novelty"] = "not_applicable"
    payload["math_result"][field] = value
    backend.queue("reviewer", CannedResponse(message=json.dumps(payload)))

    decision = Reviewer(backend).evaluate(
        objective="verify the first 100 cases",
        round_index=1,
        session_id=None,
        main_summary="finite verification complete",
        main_error=None,
        scope="bounded",
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )

    assert decision.status == "continue"
    assert issue in decision.reason


def test_non_math_reviewer_done_remains_done(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    backend = MemoryBackend()
    backend.queue(
        "reviewer",
        CannedResponse(
            message=json.dumps(
                {
                    "status": "done",
                    "reason": "ordinary task complete",
                    "next_action": "none",
                    "round_summary_markdown": "# Review\n\n- done\n",
                    "completion_summary_markdown": "Done.",
                }
            )
        ),
    )

    decision = Reviewer(backend).evaluate(
        objective="ordinary task",
        round_index=1,
        session_id=None,
        main_summary="done",
        main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )

    assert decision.status == "done"
    review_prompt, review_options = next(
        (prompt, options)
        for label, prompt, options in backend.history
        if label == "reviewer"
    )
    assert "math_result" not in review_prompt
    assert review_options.output_schema_path.endswith("reviewer_schema.json")
    assert "math_result" not in decision.to_event_payload()


def test_math_adaptation_does_not_fire_after_last_round(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "math")
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)
    backend = MemoryBackend()
    backend.queue("matcher", _match())
    for index in range(1, 3):
        backend.queue(
            f"engineer-r{index}",
            CannedResponse(message=f"attempt {index}"),
        )
        backend.queue("reviewer", _continue(index))
    backend.queue(
        "scientist.skill_distill",
        CannedResponse(message=ALTERNATIVE_SKILL),
    )
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=2,
            math_adaptive_rejection_threshold=2,
            math_adaptive_skill_max_triggers=2,
        ),
    )

    outcome = loop.run("prove the divisibility theorem", workdir=tmp_path)

    assert outcome.status == "max_rounds"
    assert not any(
        label == "scientist.skill_distill"
        for label, _, _ in backend.history
    )


def test_math_adaptation_ignores_environmental_continue(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "math")
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)
    backend = MemoryBackend()
    backend.queue("matcher", _match())
    backend.queue("engineer-r1", CannedResponse(message="attempt 1"))
    backend.queue("reviewer", _continue(1))
    backend.queue("engineer-r2", CannedResponse(message="attempt 2"))
    environmental = json.loads(_continue(2).message)
    environmental["failure_cause"] = "environmental"
    backend.queue(
        "reviewer",
        CannedResponse(message=json.dumps(environmental)),
    )
    backend.queue("engineer-r3", CannedResponse(message="proof complete"))
    backend.queue("reviewer", _done_math())
    backend.queue(
        "scientist.skill_distill",
        CannedResponse(message=ALTERNATIVE_SKILL),
    )
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=3,
            math_adaptive_rejection_threshold=2,
        ),
    )

    outcome = loop.run("prove the divisibility theorem", workdir=tmp_path)

    assert outcome.successful
    assert not any(
        label == "scientist.skill_distill"
        for label, _, _ in backend.history
    )


def test_math_adaptation_requires_consecutive_method_rejections(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "math")
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)
    backend = MemoryBackend()
    backend.queue("matcher", _match())
    backend.queue("engineer-r1", CannedResponse(message="attempt 1"))
    backend.queue("reviewer", _continue(1))
    backend.queue("engineer-r2", CannedResponse(message="incremental progress"))
    backend.queue(
        "reviewer",
        _continue(2, failure_cause="execution_mistake"),
    )
    backend.queue("engineer-r3", CannedResponse(message="attempt 3"))
    backend.queue("reviewer", _continue(3))
    backend.queue("engineer-r4", CannedResponse(message="proof complete"))
    backend.queue("reviewer", _done_math())
    backend.queue(
        "scientist.skill_distill",
        CannedResponse(message=ALTERNATIVE_SKILL),
    )
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=4,
            math_adaptive_rejection_threshold=2,
        ),
    )

    outcome = loop.run("prove the divisibility theorem", workdir=tmp_path)

    assert outcome.successful
    assert not any(
        label == "scientist.skill_distill"
        for label, _, _ in backend.history
    )


def test_math_adaptation_ignores_ordinary_continue(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "math")
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)
    backend = MemoryBackend()
    backend.queue("matcher", _match())
    backend.queue("engineer-r1", CannedResponse(message="incremental progress"))
    backend.queue("reviewer", _continue(1, failure_cause=""))
    backend.queue("engineer-r2", CannedResponse(message="proof complete"))
    backend.queue("reviewer", _done_math())
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=2,
            math_adaptive_rejection_threshold=1,
        ),
    )

    assert loop.run(
        "prove the divisibility theorem",
        workdir=tmp_path,
    ).successful
    assert not any(
        label == "scientist.skill_distill" for label, _, _ in backend.history
    )


def test_math_adaptation_trigger_limit_survives_resume_and_resets_per_mission(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "math")
    skills_dir = tmp_path / "skills"
    checkpoint_path = tmp_path / "state" / "checkpoint.json"
    _seed_skill(skills_dir)

    first = _CostedMemoryBackend(scientist_cost_usd=0.0)
    first.queue("matcher", _match())
    for index in range(1, 4):
        first.queue(f"engineer-r{index}", CannedResponse(message=f"attempt {index}"))
    first.queue("reviewer", _continue(1), _continue(2), _blocked_math())
    first.queue(
        "scientist.skill_distill",
        CannedResponse(message=ALTERNATIVE_SKILL),
    )
    first_loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=first,
        reviewer_runner=first,
        config=SkillLoopConfig(
            max_rounds=3,
            math_adaptive_rejection_threshold=2,
            math_adaptive_skill_max_triggers=1,
            math_adaptive_skill_max_cost_usd=5.0,
            checkpoint_path=checkpoint_path,
            session_id="mission-resume",
        ),
    )

    assert first_loop.run("prove the divisibility theorem", workdir=tmp_path).status == "blocked"
    assert sum(
        label == "scientist.skill_distill" for label, _, _ in first.history
    ) == 1

    resumed = _CostedMemoryBackend(scientist_cost_usd=0.0)
    resumed.queue("matcher", _match())
    for index in range(1, 4):
        resumed.queue(f"engineer-r{index}", CannedResponse(message=f"resume {index}"))
    resumed.queue("reviewer", _continue(1), _continue(2), _done_math())
    resumed_loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=resumed,
        reviewer_runner=resumed,
        config=SkillLoopConfig(
            max_rounds=3,
            math_adaptive_rejection_threshold=2,
            math_adaptive_skill_max_triggers=1,
            math_adaptive_skill_max_cost_usd=5.0,
            checkpoint_path=checkpoint_path,
            session_id="mission-resume",
        ),
    )

    assert resumed_loop.run(
        "prove the divisibility theorem",
        workdir=tmp_path,
    ).successful
    assert not any(
        label == "scientist.skill_distill" for label, _, _ in resumed.history
    )

    fresh = _CostedMemoryBackend(scientist_cost_usd=0.0)
    fresh.queue("matcher", _match())
    for index in range(1, 4):
        fresh.queue(f"engineer-r{index}", CannedResponse(message=f"fresh {index}"))
    fresh.queue("reviewer", _continue(1), _continue(2), _done_math())
    fresh.queue(
        "scientist.skill_distill",
        CannedResponse(message=ALTERNATIVE_SKILL),
    )
    fresh_loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=fresh,
        reviewer_runner=fresh,
        config=SkillLoopConfig(
            max_rounds=3,
            math_adaptive_rejection_threshold=2,
            math_adaptive_skill_max_triggers=1,
            math_adaptive_skill_max_cost_usd=5.0,
            checkpoint_path=checkpoint_path,
            session_id="mission-fresh",
        ),
    )

    assert fresh_loop.run(
        "prove the divisibility theorem",
        workdir=tmp_path,
    ).successful
    assert sum(
        label == "scientist.skill_distill" for label, _, _ in fresh.history
    ) == 1


def test_math_adaptation_budget_cap_survives_mission_resume(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "math")
    skills_dir = tmp_path / "skills"
    checkpoint_path = tmp_path / "state" / "checkpoint.json"
    _seed_skill(skills_dir)

    first = _CostedMemoryBackend(scientist_cost_usd=1.0)
    first.queue("matcher", _match())
    for index in range(1, 4):
        first.queue(f"engineer-r{index}", CannedResponse(message=f"attempt {index}"))
    first.queue("reviewer", _continue(1), _continue(2), _blocked_math())
    first.queue(
        "scientist.skill_distill",
        CannedResponse(message=ALTERNATIVE_SKILL),
    )
    first_loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=first,
        reviewer_runner=first,
        config=SkillLoopConfig(
            max_rounds=3,
            math_adaptive_rejection_threshold=2,
            math_adaptive_skill_max_triggers=2,
            math_adaptive_skill_max_cost_usd=1.0,
            checkpoint_path=checkpoint_path,
            session_id="mission-budget",
        ),
    )

    assert first_loop.run("prove the divisibility theorem", workdir=tmp_path).status == "blocked"

    resumed = _CostedMemoryBackend(scientist_cost_usd=1.0)
    resumed.queue("matcher", _match())
    for index in range(1, 4):
        resumed.queue(f"engineer-r{index}", CannedResponse(message=f"resume {index}"))
    resumed.queue("reviewer", _continue(1), _continue(2), _done_math())
    resumed_loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=resumed,
        reviewer_runner=resumed,
        config=SkillLoopConfig(
            max_rounds=3,
            math_adaptive_rejection_threshold=2,
            math_adaptive_skill_max_triggers=2,
            math_adaptive_skill_max_cost_usd=1.0,
            checkpoint_path=checkpoint_path,
            session_id="mission-budget",
        ),
    )

    assert resumed_loop.run(
        "prove the divisibility theorem",
        workdir=tmp_path,
    ).successful
    assert not any(
        label == "scientist.skill_distill" for label, _, _ in resumed.history
    )
    ledger_rows = [
        json.loads(line)
        for line in (tmp_path / "research" / "MATH_METHOD_LEDGER.jsonl")
        .read_text()
        .splitlines()
    ]
    assert any(row["status"] == "cost_cap_reached" for row in ledger_rows)


def test_math_adaptation_reserves_budget_before_crash(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "math")
    skills_dir = tmp_path / "skills"
    checkpoint_path = tmp_path / "state" / "checkpoint.json"
    mission_id = "mission-crash"
    _seed_skill(skills_dir)

    crashing = _CrashOnScientistBackend()
    crashing.queue("matcher", _match())
    crashing.queue("engineer-r1", CannedResponse(message="attempt 1"))
    crashing.queue("engineer-r2", CannedResponse(message="attempt 2"))
    crashing.queue("reviewer", _continue(1), _continue(2))
    crashing_loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=crashing,
        reviewer_runner=crashing,
        config=SkillLoopConfig(
            max_rounds=3,
            math_adaptive_rejection_threshold=2,
            math_adaptive_skill_max_triggers=2,
            math_adaptive_skill_max_cost_usd=1.0,
            checkpoint_path=checkpoint_path,
            session_id=mission_id,
        ),
    )

    with pytest.raises(KeyboardInterrupt):
        crashing_loop.run("prove the divisibility theorem", workdir=tmp_path)

    state = load_math_adaptation_state(
        math_adaptation_state_path(checkpoint_path, mission_id),
        mission_id,
    )
    assert state["trigger_count"] == 1
    assert state["spent_usd"] == pytest.approx(1.0)

    resumed = _CostedMemoryBackend(scientist_cost_usd=1.0)
    resumed.queue("matcher", _match())
    for index in range(1, 4):
        resumed.queue(f"engineer-r{index}", CannedResponse(message=f"resume {index}"))
    resumed.queue("reviewer", _continue(1), _continue(2), _done_math())
    resumed_loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=resumed,
        reviewer_runner=resumed,
        config=SkillLoopConfig(
            max_rounds=3,
            math_adaptive_rejection_threshold=2,
            math_adaptive_skill_max_triggers=2,
            math_adaptive_skill_max_cost_usd=1.0,
            checkpoint_path=checkpoint_path,
            session_id=mission_id,
        ),
    )

    assert resumed_loop.run(
        "prove the divisibility theorem",
        workdir=tmp_path,
    ).successful
    assert not any(
        label == "scientist.skill_distill" for label, _, _ in resumed.history
    )


@pytest.mark.parametrize("reported_cost", [float("nan"), 10**400])
def test_math_adaptation_invalid_settlement_keeps_reservation(
    tmp_path: Path,
    reported_cost: float | int,
) -> None:
    persist_vertical(tmp_path, "math")
    skills_dir = tmp_path / "skills"
    checkpoint_path = tmp_path / "state" / "checkpoint.json"
    mission_id = "mission-invalid-settlement"
    _seed_skill(skills_dir)
    backend = _CostedMemoryBackend(scientist_cost_usd=reported_cost)
    backend.queue("matcher", _match())
    for index in range(1, 4):
        backend.queue(f"engineer-r{index}", CannedResponse(message=f"attempt {index}"))
    backend.queue("reviewer", _continue(1), _continue(2), _done_math())
    backend.queue(
        "scientist.skill_distill",
        CannedResponse(message=ALTERNATIVE_SKILL),
    )
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=3,
            math_adaptive_rejection_threshold=2,
            math_adaptive_skill_max_triggers=2,
            math_adaptive_skill_max_cost_usd=1.0,
            checkpoint_path=checkpoint_path,
            session_id=mission_id,
        ),
    )

    assert loop.run(
        "prove the divisibility theorem",
        workdir=tmp_path,
    ).successful
    state = load_math_adaptation_state(
        math_adaptation_state_path(checkpoint_path, mission_id),
        mission_id,
    )
    assert state["spent_usd"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "corrupt_update",
    [
        {"spent_usd": float("nan")},
        {"spent_usd": 10**400},
        {"rejection_streak": [{}]},
        {
            "rejection_streak": [
                {
                    "round_index": 1,
                    "reason": "failed",
                    "next_action": "retry",
                    "nested": {"bad": float("nan")},
                }
            ]
        },
        {"method_records": [{}]},
        {
            "method_records": [
                {
                    "status": "created",
                    "trigger_index": 1,
                    "scientist_cost_usd": float("nan"),
                }
            ]
        },
    ],
)
def test_corrupt_math_adaptation_state_disables_scientist(
    tmp_path: Path,
    corrupt_update: dict,
) -> None:
    persist_vertical(tmp_path, "math")
    skills_dir = tmp_path / "skills"
    checkpoint_path = tmp_path / "state" / "checkpoint.json"
    mission_id = "mission-corrupt"
    _seed_skill(skills_dir)
    state_path = math_adaptation_state_path(checkpoint_path, mission_id)
    state_path.parent.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "mission_id": mission_id,
        "trigger_count": 0,
        "spent_usd": 0.0,
        "rejection_streak": [],
        "method_records": [],
        **corrupt_update,
    }
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    original = state_path.read_text()

    backend = MemoryBackend()
    backend.queue("matcher", _match())
    for index in range(1, 4):
        backend.queue(f"engineer-r{index}", CannedResponse(message=f"attempt {index}"))
    backend.queue("reviewer", _continue(1), _continue(2), _done_math())
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=3,
            math_adaptive_rejection_threshold=2,
            checkpoint_path=checkpoint_path,
            session_id=mission_id,
        ),
    )

    assert loop.run(
        "prove the divisibility theorem",
        workdir=tmp_path,
    ).successful
    assert not any(
        label == "scientist.skill_distill" for label, _, _ in backend.history
    )
    assert state_path.read_text() == original
