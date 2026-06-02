"""Reviewer-driven self-evolution from FAILURE + provisional skills.

The operator's contract:

* Whether a failed mission teaches a skill is the REVIEWER's decision, not a
  status heuristic. The reviewer emits ``failure_cause`` and, only for a
  fixable ``skill_gap`` (e.g. wrong RL hyperparameters — not a dead idea), a
  reusable ``mission_lesson``. A ``method_failure`` (doomed idea) teaches
  NOTHING.
* A skill born from a failure (or a freshly-distilled skill whose first
  outing failed) is PROVISIONAL: it is retained for good only once a later
  mission REUSES it and succeeds. Repeated non-environmental reuse failures
  archive it.
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

REVISED_SKILL_MD = SKILL_MD + (
    "\n## Failure lessons\n- A prior mission was BLOCKED because it tried to run "
    "shell commands instead of replying inline; never invoke tools for a pure "
    "greeting, just emit the one-line message directly so the reviewer can "
    "confirm completion without acceptance-check failures.\n"
)

_LESSON = (
    "For a pure conversational greeting task, never invoke shell tools or open "
    "editors; reply inline with a single greeting line so the acceptance "
    "criterion can be confirmed. Running tools is the recurring failure mode."
)


def _blocked_review(*, failure_cause: str = "skill_gap",
                    mission_lesson: str = _LESSON) -> str:
    payload = {
        "status": "blocked",
        "confidence": 0.92,
        "reason": "Engineer kept invoking shell tools for a greeting task.",
        "next_action": (
            "Stop running shell commands; reply inline with a single greeting "
            "line and confirm the output so the criterion can be checked."
        ),
        "round_summary_markdown": (
            "# Review Summary\n\n- Engineer ran tools instead of replying.\n"
            "- Blocked: the task needs an inline greeting, not tool use.\n"
        ),
        "completion_summary_markdown": "",
        "failure_cause": failure_cause,
    }
    if mission_lesson:
        payload["mission_lesson"] = mission_lesson
    return json.dumps(payload)


def _done_review() -> str:
    return json.dumps({
        "status": "done",
        "confidence": 0.95,
        "reason": "Greeting was produced as required.",
        "next_action": "No further action needed.",
        "round_summary_markdown": "# Review Summary\n\n- Greeting printed.\n",
        "completion_summary_markdown": "Done.",
    })


def _seed_skill(skills_dir: Path, *, provisional: bool = False) -> SkillStore:
    store = SkillStore(skills_dir)
    store.save_distilled(
        task_description="say hi to the user",
        raw_distill_output=SKILL_MD,
        scientist_model="memory",
        provisional=provisional,
    )
    assert any(
        s["name"] == "Write a hello message" for s in store.list_summaries()
    )
    return store


def _match_hello() -> CannedResponse:
    return CannedResponse(message=json.dumps({
        "matched": [{"name": "Write a hello message", "fit": "high",
                     "why": "greeting task"}],
    }))


# ---------------------------------------------------------------------------
# Reviewer DECIDES whether to evolve
# ---------------------------------------------------------------------------

def test_skill_gap_lesson_merges_into_matched_skill(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)

    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="Ran a shell tool."))
    backend.queue("reviewer", CannedResponse(message=_blocked_review()))
    backend.queue("distiller.revise.failure_lesson",
                  CannedResponse(message=REVISED_SKILL_MD))

    events: list[dict] = []
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1, distill_on_miss=False,
                               skill_revise_on_failure=True),
        on_event=events.append,
    )
    outcome = loop.run("say hi to the user", workdir=tmp_path)

    assert outcome.status == "blocked", outcome
    assert any(e.get("type") == "skill.lesson" for e in events), [
        e.get("type") for e in events
    ]
    revise_calls = [p for label, p, _ in backend.history
                    if label == "distiller.revise.failure_lesson"]
    assert len(revise_calls) == 1
    store = SkillStore(skills_dir)
    summary = next(s for s in store.list_summaries()
                   if s["name"] == "Write a hello message")
    assert "Failure lessons" in store.load(summary["path"]).render()


def test_method_failure_teaches_nothing(tmp_path: Path) -> None:
    """A reviewer ``method_failure`` (dead idea, no lesson) must NOT evolve
    any skill — this is the false-learning guard the operator asked for."""
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)

    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="Idea fundamentally fails."))
    backend.queue("reviewer", CannedResponse(
        message=_blocked_review(failure_cause="method_failure", mission_lesson="")))

    events: list[dict] = []
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1, distill_on_miss=False,
                               skill_revise_on_failure=True),
        on_event=events.append,
    )
    loop.run("say hi to the user", workdir=tmp_path)

    assert not any(e.get("type", "").startswith("skill.lesson") for e in events)
    assert not any(label == "distiller.revise.failure_lesson"
                   for label, _, _ in backend.history)


def test_lesson_deduplicated_across_missions(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)

    def _run_blocked() -> list[dict]:
        backend = MemoryBackend()
        backend.queue("matcher", _match_hello())
        backend.queue("engineer-r1", CannedResponse(message="Ran a shell tool."))
        backend.queue("reviewer", CannedResponse(message=_blocked_review()))
        backend.queue("distiller.revise.failure_lesson",
                      CannedResponse(message=REVISED_SKILL_MD))
        events: list[dict] = []
        loop = SkillLoop(
            skills_dir=skills_dir,
            engineer_runner=backend,
            reviewer_runner=backend,
            config=SkillLoopConfig(max_rounds=1, distill_on_miss=False,
                                   skill_revise_on_failure=True),
            on_event=events.append,
        )
        loop.run("say hi to the user", workdir=tmp_path)
        return events

    first = _run_blocked()
    assert any(e.get("type") == "skill.lesson" for e in first)

    second = _run_blocked()
    assert any(e.get("type") == "skill.lesson.skipped_duplicate" for e in second)
    assert not any(e.get("type") == "skill.lesson" for e in second)


def test_failure_evolution_disabled_by_default(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)

    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="Ran a shell tool."))
    backend.queue("reviewer", CannedResponse(message=_blocked_review()))

    events: list[dict] = []
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1, distill_on_miss=False),
        on_event=events.append,
    )
    loop.run("say hi to the user", workdir=tmp_path)

    assert not any(e.get("type", "").startswith("skill.lesson") for e in events)
    assert not any(label == "distiller.revise.failure_lesson"
                   for label, _, _ in backend.history)


# ---------------------------------------------------------------------------
# Provisional skill lifecycle
# ---------------------------------------------------------------------------

def test_fresh_distilled_skill_failure_marks_provisional(tmp_path: Path) -> None:
    """A skill born this mission whose first outing fails is unproven: it is
    marked provisional and absorbs the reviewer lesson."""
    skills_dir = tmp_path / "skills"

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))  # distill-on-miss
    backend.queue("engineer-r1", CannedResponse(message="Ran a shell tool."))
    backend.queue("reviewer", CannedResponse(message=_blocked_review()))
    backend.queue("distiller.revise.failure_lesson",
                  CannedResponse(message=REVISED_SKILL_MD))

    events: list[dict] = []
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1, distill_on_miss=True,
                               skill_revise_on_failure=True),
        on_event=events.append,
    )
    loop.run("say hi to the user", workdir=tmp_path)

    assert any(e.get("type") == "skill.provisional.marked" for e in events), [
        e.get("type") for e in events
    ]
    store = SkillStore(skills_dir)
    summary = next(s for s in store.list_summaries()
                   if s["name"] == "Write a hello message")
    assert store.load(summary["path"]).provisional is True


def test_provisional_skill_confirmed_on_successful_reuse(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir, provisional=True)

    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="hello world"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1, distill_on_miss=False,
                               skill_writeback=True,
                               skill_revise_on_failure=True),
        on_event=events.append,
    )
    outcome = loop.run("say hi to the user", workdir=tmp_path)

    assert outcome.status == "done", outcome
    assert any(e.get("type") == "skill.confirmed" for e in events), [
        e.get("type") for e in events
    ]
    store = SkillStore(skills_dir)
    summary = next(s for s in store.list_summaries()
                   if s["name"] == "Write a hello message")
    assert store.load(summary["path"]).provisional is False


def test_provisional_skill_retired_after_repeated_reuse_failures(
    tmp_path: Path,
) -> None:
    """A provisional skill that keeps failing on reuse is archived once it
    crosses the prune threshold, and no longer appears as a candidate."""
    skills_dir = tmp_path / "skills"
    store = _seed_skill(skills_dir, provisional=True)
    # Pre-load one prior failure so a single more failure crosses threshold (2).
    summary = next(s for s in store.list_summaries()
                   if s["name"] == "Write a hello message")
    skill = store.load(summary["path"])
    skill.provisional_failures = 1
    store.save(skill)

    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="Ran a shell tool."))
    backend.queue("reviewer", CannedResponse(message=_blocked_review()))
    backend.queue("distiller.revise.failure_lesson",
                  CannedResponse(message=REVISED_SKILL_MD))

    events: list[dict] = []
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1, distill_on_miss=False,
                               skill_revise_on_failure=True),
        on_event=events.append,
    )
    loop.run("say hi to the user", workdir=tmp_path)

    assert any(e.get("type") == "skill.provisional.retired" for e in events), [
        e.get("type") for e in events
    ]
    # The archived skill must NOT re-enter the candidate pool.
    fresh = SkillStore(skills_dir)
    assert not any(s["name"] == "Write a hello message"
                   for s in fresh.list_summaries())


# ---------------------------------------------------------------------------
# Store-level provisional unit coverage
# ---------------------------------------------------------------------------

def test_skill_provisional_fields_round_trip() -> None:
    from argus_skill.skills.store import Skill

    skill = Skill(name="x", description="d", category="c", content="# body\nok",
                  provisional=True, provisional_failures=3)
    parsed = Skill.parse(skill.render())
    assert parsed.provisional is True
    assert parsed.provisional_failures == 3

    # Legacy frontmatter (no provisional keys) parses as confirmed.
    legacy = Skill(name="y", description="d", category="c", content="# body\nok")
    legacy_parsed = Skill.parse(legacy.render())
    assert legacy_parsed.provisional is False
    assert legacy_parsed.provisional_failures == 0


def test_confirm_and_record_provisional(tmp_path: Path) -> None:
    store = _seed_skill(tmp_path / "skills", provisional=True)
    summary = next(s for s in store.list_summaries()
                   if s["name"] == "Write a hello message")
    skill = store.load(summary["path"])

    assert skill.provisional is True
    assert store.confirm_provisional(skill) is True
    assert store.load(skill.path).provisional is False
    # Idempotent: confirming a confirmed skill is a no-op.
    assert store.confirm_provisional(skill) is False
