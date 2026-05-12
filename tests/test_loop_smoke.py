"""End-to-end smoke test of SkillLoop with the memory backend.

Verifies the integration: matcher miss → distill → 2 engineer rounds
(continue → done) → skill written back. This is the single most
important test in argus-skill — if it fails, the loop is broken at the
top level.
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


def _continue_review() -> str:
    return json.dumps({
        "status": "continue",
        "confidence": 0.4,
        "reason": "Engineer started but did not yet meet the criterion.",
        "next_action": "Print the actual greeting and confirm.",
        "round_summary_markdown": "# Review Summary\n\n- Round 1 incomplete.\n",
        "completion_summary_markdown": "",
    })


def _done_review() -> str:
    return json.dumps({
        "status": "done",
        "confidence": 0.95,
        "reason": "Greeting was produced as required.",
        "next_action": "No further action needed.",
        "round_summary_markdown": "# Review Summary\n\n- Greeting printed.\n",
        "completion_summary_markdown": "Done.",
    })


def test_skill_loop_distill_then_two_rounds_to_done(tmp_path: Path) -> None:
    backend = MemoryBackend()
    # Matcher miss
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    # Scientist distills the skill
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    # Round 1: engineer makes some progress; reviewer says continue
    backend.queue("engineer-r1", CannedResponse(message="Read the task. Will print greeting next."))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))
    # Round 2: engineer finishes; reviewer says done
    backend.queue("engineer-r2", CannedResponse(
        message="Done: printed 'hello world'. Verified output. Remaining: none.",
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    skills_dir = tmp_path / "skills"
    loop = SkillLoop(
        skills_dir=skills_dir,
        scientist_runner=backend,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=4, distill_on_miss=True, skill_writeback=True),
    )
    outcome = loop.run("say hi to the user", workdir=tmp_path)

    assert outcome.successful, f"expected done, got {outcome.status}: {outcome.reason}"
    assert outcome.round_count == 2, outcome
    assert outcome.skill_distilled is True
    assert outcome.skill_used is not None
    assert outcome.rounds[0].review.status == "continue"
    assert outcome.rounds[1].review.status == "done"

    # Reviewer next_action from round 1 must reach the engineer in round 2.
    r2_prompt = next(
        prompt for label, prompt, _ in backend.history if label == "engineer-r2"
    )
    assert "Print the actual greeting" in r2_prompt

    # Skill must have been persisted on disk.
    store = SkillStore(skills_dir)
    summaries = store.list_summaries()
    assert any(s["name"] == "Write a hello message" for s in summaries), summaries

    # On a second run with the same task, the matcher should now find the skill.
    backend2 = MemoryBackend()
    # Matcher: pick the persisted skill by name.
    backend2.queue(
        "matcher",
        CannedResponse(
            message=json.dumps({
                "matched": [{"name": "Write a hello message", "fit": "high",
                             "why": "exact match"}],
            }),
        ),
    )
    backend2.queue("engineer-r1", CannedResponse(
        message="Done: printed 'hello there'. Verified output. Remaining: none.",
    ))
    backend2.queue("reviewer", CannedResponse(message=_done_review()))

    loop2 = SkillLoop(
        skills_dir=skills_dir,
        scientist_runner=backend2,
        engineer_runner=backend2,
        reviewer_runner=backend2,
        config=SkillLoopConfig(max_rounds=3, distill_on_miss=True, skill_writeback=True),
    )
    outcome2 = loop2.run("say hi to the user", workdir=tmp_path)
    assert outcome2.successful
    assert outcome2.round_count == 1
    assert outcome2.skill_distilled is False, "should reuse, not redistill"
    assert outcome2.skill_used == "Write a hello message"


def test_skill_loop_blocked_short_circuits(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="Cannot proceed: missing API key."))
    backend.queue("reviewer", CannedResponse(message=json.dumps({
        "status": "blocked",
        "confidence": 0.99,
        "reason": "Missing required credential.",
        "next_action": "Provide API key.",
        "round_summary_markdown": "# Review\n\n- blocked on credential\n",
        "completion_summary_markdown": "",
    })))

    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        scientist_runner=backend,
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
        scientist_runner=backend,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=3),
    )
    outcome = loop.run("hard task", workdir=tmp_path)
    assert outcome.status == "max_rounds"
    assert outcome.round_count == 3


def test_skill_loop_no_distill_falls_back_to_no_skill(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("engineer-r1", CannedResponse(
        message="Done: solved without a skill. Remaining: none.",
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        scientist_runner=backend,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=2, distill_on_miss=False),
    )
    outcome = loop.run("trivial task", workdir=tmp_path)
    assert outcome.successful
    assert outcome.skill_used is None
    assert outcome.skill_distilled is False
