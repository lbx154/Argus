"""End-to-end smoke test of SkillLoop with the memory backend.

Verifies the integration: matcher hit → skill injected → 2 engineer rounds
(continue → done) → skill written back. This is the single most
important test in argus-skill — if it fails, the loop is broken at the
top level. Skill creation is no longer proactive on a matcher miss (that
minted a throwaway playbook for every trivial task); a missed match just
runs the engineer without a skill, and authoring is reviewer-gated
(see test_loop_failure_lesson).
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig, SkillStore
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend

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


def test_skill_loop_defaults_use_xhigh_reasoning_effort() -> None:
    config = SkillLoopConfig()

    assert config.engineer_model == "gpt-5.5"
    assert config.engineer_reasoning_effort == "xhigh"
    assert config.matcher_reasoning_effort == "high"
    assert config.reviewer_reasoning_effort == "xhigh"


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


def _seed_skill(skills_dir: Path, *, provisional: bool = False) -> None:
    store = SkillStore(skills_dir)
    store.save_distilled(
        task_description="say hi to the user",
        raw_distill_output=SKILL_MD,
        provisional=provisional,
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

    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=4),
    )
    outcome = loop.run("say hi to the user", workdir=tmp_path)

    assert outcome.successful, f"expected done, got {outcome.status}: {outcome.reason}"
    assert outcome.round_count == 2, outcome
    assert outcome.skill_distilled is False  # creation is reviewer-gated, never on a miss
    assert outcome.skill_used == "Write a hello message"
    assert outcome.rounds[0].review.status == "continue"
    assert outcome.rounds[1].review.status == "done"

    # Reviewer next_action from round 1 must reach the engineer in round 2.
    r2_prompt = next(
        prompt for label, prompt, _ in backend.history if label == "engineer-r2"
    )
    assert "Print the actual greeting" in r2_prompt

    # Skill is still present and was reused, not re-created.
    store = SkillStore(skills_dir)
    summaries = store.list_summaries()
    assert any(s["name"] == "Write a hello message" for s in summaries), summaries


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


def test_skill_loop_scientist_distills_candidate_on_miss(tmp_path: Path) -> None:
    """A matcher miss authors a web-enabled provisional skill; the creation
    mission may use it but cannot self-confirm it."""
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("scientist.skill_distill", CannedResponse(message="""# Solve Trivial Task
## Description
Reusable playbook for solving simple deterministic tasks.
## Category
general
## When to use
- Use when a task has no matched skill but has a small deterministic goal.
## When NOT to use
- Do not use for broad multi-stage work.
## How to solve
1. Read the task.
2. Do the smallest correct action.
## Pitfalls
- Do not invent extra scope.
## Sources
- [Python documentation](https://docs.python.org/3/) — deterministic execution basics.
- [Git documentation](https://git-scm.com/docs) — reproducible change tracking.
"""))
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
    assert outcome.skill_used == "Solve Trivial Task"
    assert outcome.skill_distilled is True
    summaries = SkillStore(tmp_path / "skills").list_summaries()
    assert [s["name"] for s in summaries] == ["Solve Trivial Task"]
    assert summaries[0]["provisional"] is True
    assert any(event.get("type") == "skill.awaiting_reuse" for event in loop_events)
    scientist_options = next(
        options for label, _prompt, options in backend.history
        if label == "scientist.skill_distill"
    )
    assert scientist_options.live_search is True

def test_render_skill_playbook_injects_all_high_fit_skills(tmp_path: Path) -> None:
    from argus_skill.skills.store import Skill

    skills_dir = tmp_path / "skills"
    store = SkillStore(skills_dir)

    def _mk(name: str, desc: str) -> Skill:
        return Skill(
            name=name,
            description=desc,
            category="demo",
            content="## When to use\n- demo tasks\n\n## How to solve\n- step 1\n",
            version=1,
            created_at="2026-05-03T00:00:00+00:00",
        )

    loop = SkillLoop(
        skills_dir=skills_dir,
        skill_store=store,
        engineer_runner=MemoryBackend(),
        reviewer_runner=MemoryBackend(),
        config=SkillLoopConfig(max_rounds=1),
    )

    one = _mk("Alpha Skill", "do alpha")
    two = _mk("Beta Skill", "do beta")

    # Single match: plain render (content only), no multi-candidate framing.
    single = loop._render_skill_playbook([one])
    assert "How to solve" in single
    assert "candidates, not orders" not in single
    assert "### Candidate skill:" not in single

    # Multiple high-fit matches: all bodies injected, agent judges relevance.
    multi = loop._render_skill_playbook([one, two])
    assert "Alpha Skill" in multi
    assert "Beta Skill" in multi
    assert "candidates, not orders" in multi
    assert "### Candidate skill: Alpha Skill" in multi
    assert "### Candidate skill: Beta Skill" in multi

    # Empty match: empty playbook.
    assert loop._render_skill_playbook([]) == ""
