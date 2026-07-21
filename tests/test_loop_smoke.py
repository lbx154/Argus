"""End-to-end smoke test of SkillLoop with the memory backend.

Verifies the integration: matcher hit → skill injected → 2 engineer rounds
(continue → done) → skill written back. This is the single most
important test in argus-skill — if it fails, the loop is broken at the
top level. On a matcher miss the Scientist may author an immediately active
project-layer skill, and the loop records the reviewed use for later evolution.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig, SkillStore
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.life.context_packet import create_mission_context
from argus_skill.loop import _nearest_transfer_scores

SKILL_MD = (
    "## Title\nWrite a hello message\n\n"
    "## Description\nGenerate a friendly greeting for any user-facing context — "
    "this is the canonical playbook for any interaction where the agent should "
    "respond with a short, well-formed acknowledgement message rather than "
    "running tools or modifying files.\n\n"
    "## Category\nhello\n\n"
    "## When to use\n- user asks to say hi or greet someone\n"
    "- user requests a friendly reply\n"
    "- the live objective is purely conversational and no work is required\n\n"
    "## When NOT to use\n- user wants production code or files modified\n"
    "- the task description references writing tests or shipping a CLI\n"
    "- the operator asks for analysis, debugging, or refactoring\n\n"
    "## How to solve\n- Read the task and identify the desired tone.\n"
    "- Compose a one-line greeting that answers without filler.\n"
    "- Do not run shell commands or open editors.\n\n"
    "## Examples\n- 'say hi' → reply with 'hello world'\n"
    "- 'greet the user politely' → reply with 'Hi there — happy to help!'\n"
    "- 'wave back at me' → reply with a single short greeting line\n\n"
    "## Response shape\n- Reply inline with the greeting only.\n"
    "- No code blocks, no tool invocations.\n"
)


def test_skill_loop_defaults_use_adaptive_reasoning_effort() -> None:
    config = SkillLoopConfig()

    assert config.engineer_model == "gpt-5.5"
    assert config.engineer_initial_reasoning_effort == "high"
    assert config.engineer_reasoning_effort == "xhigh"
    assert config.matcher_reasoning_effort == "low"
    assert config.reviewer_reasoning_effort == "high"
    assert config.nearest_transfer_enabled is False
    assert config.require_post_task_learning is True
    assert config.force_post_task_learning is False
    # Staged/paper work stays xhigh from round one; direct work opts into high.
    assert config.resolved_initial_engineer_effort() == "xhigh"
    config.workflow_mode = "direct"
    assert config.resolved_initial_engineer_effort() == "high"


def test_selective_post_task_learning_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING", "0")

    assert SkillLoopConfig().require_post_task_learning is False


def test_selective_post_task_learning_honors_persisted_disable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus_skill.core.knob_store import write_persisted_knob

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING", raising=False)
    assert write_persisted_knob("ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING", "off")

    assert SkillLoopConfig().require_post_task_learning is False


def test_nearest_transfer_ignores_self_reinforcing_task_history() -> None:
    task = (
        "Caching middleware fails to initialize because Go shadowing leaves the "
        "evaluation cacher nil. Repair cache middleware startup and verification."
    )
    summaries = [
        {
            "name": "Flipt Audit Resource Type Wiring",
            "description": "Add audit resource types and checker nouns.",
            "category": "Go audit logging",
            # A repeatedly mis-selected Skill can accumulate the current task in
            # history. Retrieval must not treat that as semantic relevance.
            "task_history": [task] * 8,
        },
        {
            "name": "Flipt Evaluation Cache Wiring",
            "description": (
                "Wire evaluation data and requests through the cache layer and "
                "evaluation middleware."
            ),
            "category": "Go evaluation caching",
            "task_history": [],
        },
    ]

    scores = _nearest_transfer_scores(task, summaries)

    assert scores[1] > scores[0]


def _continue_review() -> str:
    return json.dumps({
        "status": "continue",
        "reason": "Engineer started but did not yet meet the criterion.",
        "next_action": "Print the actual greeting and confirm.",
        "round_summary_markdown": "# Review Summary\n\n- Round 1 incomplete.\n",
        "completion_summary_markdown": "",
    })


def _done_review() -> str:
    return json.dumps({
        "status": "done",
        "reason": "Greeting was produced as required.",
        "next_action": "No further action needed.",
        "round_summary_markdown": "# Review Summary\n\n- Greeting printed.\n",
        "completion_summary_markdown": "Done.",
    })


def _scope_change_review() -> str:
    return json.dumps({
        "status": "continue",
        "reason": "The baseline exposed a defect outside this mission's non-goals.",
        "next_action": "Authorize a scoped correctness-repair mission before rerunning the baseline.",
        "round_summary_markdown": "# Review Summary\n\n- A separate repair mission is required.\n",
        "completion_summary_markdown": "",
        "planner_report": {
            "forward_progress": True,
            "headline": "The baseline found a reproducible platform defect.",
            "blocker": "Repair is outside the current mission contract.",
            "recommended_next": "Insert a scoped correctness-repair mission.",
            "plan_signal": "continue",
            "plan_signal_reason": "",
            "evidence_files": [],
        },
    })


def _seed_skill(skills_dir: Path) -> None:
    store = SkillStore(skills_dir)
    store.save_distilled(
        task_description="say hi to the user",
        raw_distill_output=SKILL_MD,
    )


def _match_hello() -> CannedResponse:
    return CannedResponse(message=json.dumps({
        "matched": [{"name": "Write a hello message", "fit": "high",
                     "why": "greeting task"}],
    }))


def test_skill_loop_matched_then_two_rounds_to_done(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)

    backend = MemoryBackend()
    # Matcher hit on the curated skill (no proactive distill-on-miss any more).
    backend.queue("matcher", _match_hello())
    # Round 1: engineer makes some progress; reviewer says continue
    backend.queue("engineer-r1", CannedResponse(message="Read the task. Will print greeting next."))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))
    # Round 2: engineer finishes; reviewer says done
    backend.queue("engineer-r2", CannedResponse(
        message="Done: printed 'hello world'. Verified output. Remaining: none.",
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    mission_context = create_mission_context(
        life_dir=tmp_path / "state",
        mission_id="hello-mission",
        stage="direct",
        scope="bounded",
        objective="say hi to the user",
        acceptance_check="the greeting is printed",
        context_refs=[{
            "kind": "artifact",
            "ref": "request.txt",
            "why": "requested wording",
            "content_hash": "",
        }],
    )
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=4,
            checkpoint_path=mission_context.parent / "CHECKPOINT.md",
            context_packet_path=str(mission_context),
        ),
    )
    outcome = loop.run("say hi to the user", workdir=tmp_path)

    assert outcome.successful, f"expected done, got {outcome.status}: {outcome.reason}"
    assert outcome.round_count == 2, outcome
    assert outcome.skill_distilled is False  # creation is reviewer-gated, never on a miss
    assert outcome.skill_used == "Write a hello message"
    assert outcome.rounds[0].review.status == "continue"
    assert outcome.rounds[1].review.status == "done"
    assert [label for label, _prompt, _options in backend.history].count("matcher") == 1

    # Continuation rounds omit the large static contract but retain the short
    # concrete Reviewer instruction alongside CHECKPOINT.md.
    r2_prompt = next(
        prompt for label, prompt, _ in backend.history if label == "engineer-r2"
    )
    assert "Print the actual greeting" in r2_prompt
    assert "## Continuation turn" in r2_prompt
    assert "## Current mission task" not in r2_prompt
    assert str(mission_context.parent / "latest.json") in r2_prompt

    latest = json.loads((mission_context.parent / "latest.json").read_text())
    assert latest["kind"] == "round_reviewed_handoff"
    assert latest["round"] == 2
    assert latest["scope"] == "bounded"
    assert latest["objective"] == "say hi to the user"
    assert latest["acceptance_check"] == "the greeting is printed"
    assert latest["context_refs"][0]["ref"] == "request.txt"
    assert latest["mission"]["path"] == str(mission_context)
    assert latest["review"]["status"] == "done"
    assert (mission_context.parent / "round-0001-engineer.json").exists()
    assert (mission_context.parent / "round-0001.json").exists()
    assert (mission_context.parent / "round-0002.json").exists()

    # Skill is still present and was reused, not re-created.
    store = SkillStore(skills_dir)
    summaries = store.list_summaries()
    assert any(s["name"] == "Write a hello message" for s in summaries), summaries


def test_scope_changing_reviewer_guidance_escalates_without_second_engineer_round(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)
    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="reproduced a platform defect"))
    backend.queue("reviewer", CannedResponse(message=_scope_change_review()))
    events: list[dict] = []

    outcome = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=3, skill_adapter_enabled=False),
        on_event=events.append,
    ).run("produce a baseline only", workdir=tmp_path)

    assert outcome.status == "replan_requested"
    assert outcome.round_count == 1
    assert not any(label == "engineer-r2" for label, _prompt, _opts in backend.history)
    review = outcome.rounds[-1].review
    assert review.status == "replan_requested"
    assert review.planner_report["plan_signal"] == "reconsider"
    assert review.planner_report["stage_reconciliation_required"] is True
    assert review.planner_report["mission_scope_change_required"] is True
    assert any(event.get("type") == "round.review.scope_change_escalated" for event in events)


def test_direct_work_escalates_engineer_effort_only_after_review(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)
    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="partial"))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))
    backend.queue("engineer-r2", CannedResponse(message="done"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    outcome = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=2,
            workflow_mode="direct",
            skill_adapter_enabled=False,
        ),
    ).run("say hi to the user", workdir=tmp_path)

    assert outcome.successful
    options = {label: opts for label, _prompt, opts in backend.history}
    assert options["engineer-r1"].reasoning_effort == "high"
    assert options["engineer-r2"].reasoning_effort == "xhigh"
    assert options["reviewer"].reasoning_effort == "high"


def test_matched_skill_is_adapted_with_one_low_effort_call(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)
    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue(
        "skill-adapter",
        CannedResponse(
            message="- Emit exactly one concise greeting.\n- Preserve the requested tone.",
            input_tokens=40,
            output_tokens=12,
        ),
    )
    backend.queue("engineer-r1", CannedResponse(message="done"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))
    events: list[dict] = []
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1),
        on_event=events.append,
    )

    outcome = loop.run("say hi warmly", workdir=tmp_path)

    assert outcome.successful
    adapter_prompt, adapter_options = next(
        (prompt, options)
        for label, prompt, options in backend.history
        if label == "skill-adapter"
    )
    assert "Closest reusable skill" in adapter_prompt
    assert "at most 8 short bullets" in adapter_prompt
    assert adapter_options.reasoning_effort == "low"
    engineer_prompt = next(
        prompt for label, prompt, _options in backend.history if label == "engineer-r1"
    )
    assert "Task-adapted skill guideline" in engineer_prompt
    assert "Emit exactly one concise greeting" in engineer_prompt
    reviewer_prompt = next(
        prompt for label, prompt, _options in backend.history if label == "reviewer"
    )
    assert "Engineer skill pointer (on demand)" in reviewer_prompt
    assert "Write a hello message" in reviewer_prompt
    assert "Expected version/hash" in reviewer_prompt
    assert "sha256:" in reviewer_prompt
    assert "Do not read it by default" in reviewer_prompt
    assert "## Examples" not in reviewer_prompt
    pointer = reviewer_prompt.split("## Engineer skill pointer (on demand)", 1)[1]
    assert len(pointer.split("## Stage checklist", 1)[0]) < 500
    assert any(event.get("type") == "skill.transfer.completed" for event in events)


def test_skill_adapter_reasoning_effort_honors_operator_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_ADAPTER_REASONING_EFFORT", "xhigh")
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)
    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue(
        "skill-adapter",
        CannedResponse(message="- Emit one concise greeting."),
    )
    backend.queue("engineer-r1", CannedResponse(message="done"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))
    events: list[dict] = []

    outcome = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1),
        on_event=events.append,
    ).run("say hi warmly", workdir=tmp_path)

    assert outcome.successful
    adapter_options = next(
        options
        for label, _prompt, options in backend.history
        if label == "skill-adapter"
    )
    assert adapter_options.reasoning_effort == "xhigh"
    started = next(
        event for event in events if event.get("type") == "skill.transfer.started"
    )
    assert started["reasoning_effort"] == "xhigh"


def test_low_confidence_transfer_uses_compact_hint_without_adapter(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("engineer-r1", CannedResponse(message="implemented and verified"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    outcome = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=1,
            require_post_task_learning=True,
            nearest_transfer_enabled=True,
            nearest_transfer_min_score=1.0,
        ),
    ).run("repair a database migration", workdir=tmp_path)

    assert outcome.successful
    labels = [label for label, _prompt, _options in backend.history]
    assert labels.count("matcher") == 1
    assert "skill-adapter" not in labels
    prompt = next(
        prompt for label, prompt, _options in backend.history if label == "engineer-r1"
    )
    assert "Low-confidence transfer hint" in prompt
    assert "Treat this only as an analogy" in prompt


def test_live_manager_guidance_is_injected_at_next_engineer_round(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)
    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="done"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1),
        extra_guidance_provider=lambda: [
            "[MANAGER STEERING] invent a mathematical tool before more checking"
        ],
    )
    loop.run("say hi to the user", workdir=tmp_path)

    prompt = next(
        prompt for label, prompt, _ in backend.history if label == "engineer-r1"
    )
    assert "LIVE MANAGER / OPERATOR DIRECTIVES" in prompt
    assert "invent a mathematical tool" in prompt


def test_live_guidance_cannot_silently_broaden_bounded_task(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)
    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="done"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1),
        extra_guidance_provider=lambda: ["start profiling instead"],
    )
    loop.run("certify baseline only", workdir=tmp_path, scope="bounded")

    prompt = next(
        prompt for label, prompt, _ in backend.history if label == "engineer-r1"
    )
    assert "do not silently broaden a structured bounded task" in prompt
    assert "request Reviewer/Planner replanning" in prompt


def test_skill_loop_blocked_short_circuits(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="Cannot proceed: missing API key."))
    backend.queue("reviewer", CannedResponse(message=json.dumps({
        "status": "blocked",
        "reason": "Missing required credential.",
        "next_action": "Provide API key.",
        "round_summary_markdown": "# Review\n\n- blocked on credential\n",
        "completion_summary_markdown": "",
    })))

    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=5),
    )
    outcome = loop.run("call the proprietary API", workdir=tmp_path)
    assert outcome.status == "blocked"
    assert outcome.round_count == 1
    assert "credential" in outcome.reason.lower()


def test_skill_loop_max_rounds_hit(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    # Every round produces a continue verdict; loop should bail at max_rounds.
    for i in range(1, 4):
        backend.queue(f"engineer-r{i}", CannedResponse(message=f"Read inputs (round {i})."))
        backend.queue("reviewer", CannedResponse(message=_continue_review()))

    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=3),
    )
    outcome = loop.run("hard task", workdir=tmp_path)
    assert outcome.status == "max_rounds"
    assert outcome.round_count == 3


def test_repeated_rejections_do_not_spawn_separate_scientist(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)
    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    for i in range(1, 5):
        backend.queue(f"engineer-r{i}", CannedResponse(message=f"attempt {i}"))
        backend.queue("reviewer", CannedResponse(message=_continue_review()))
    backend.queue("engineer-r5", CannedResponse(message="new strategy succeeded"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=5, adaptive_skill_interval=4),
        on_event=events.append,
    )
    outcome = loop.run("say hi to the user", workdir=tmp_path)

    assert outcome.successful
    labels = [label for label, _prompt, _options in backend.history]
    assert "scientist.skill_distill" not in labels
    assert not any(e.get("type") == "skill.scientist.adaptation_created" for e in events)


def test_skill_loop_matcher_miss_defers_creation_to_engineer(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("engineer-r1", CannedResponse(
        message="Done: solved with the scientist skill. Remaining: none.",
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    loop_events: list[dict] = []
    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=2),
        on_event=loop_events.append,
    )
    outcome = loop.run("trivial task", workdir=tmp_path)
    assert outcome.successful
    assert outcome.skill_used is None
    assert outcome.skill_distilled is False
    summaries = SkillStore(tmp_path / "skills").list_summaries()
    assert summaries == []
    labels = [label for label, _prompt, _options in backend.history]
    assert "matcher" in labels
    assert "scientist.skill_distill" not in labels


def test_direct_workflow_uses_matcher_and_skips_separate_scientist(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue(
        "engineer-r1",
        CannedResponse(message="Delivered the requested standalone artifact."),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review()))
    events: list[dict] = []
    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=2, workflow_mode="direct"),
        on_event=events.append,
    )

    outcome = loop.run("write one short poem", workdir=tmp_path)

    assert outcome.successful
    labels = [label for label, _prompt, _options in backend.history]
    assert "matcher" in labels
    assert "scientist.skill_distill" not in labels
    assert not any(event.get("type") == "skill.scientist.started" for event in events)
