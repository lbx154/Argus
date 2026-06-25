"""Reviewer-driven self-evolution from FAILURE + provisional skills.

The operator's contract:

* Whether a failed mission teaches a skill is the REVIEWER's decision, not a
  status heuristic. The reviewer emits ``failure_cause`` and, only for a
  fixable ``skill_gap`` (e.g. wrong RL hyperparameters — not a dead idea), a
  reusable ``mission_lesson``. A ``method_failure`` (doomed idea) teaches
  NOTHING.
* Every skill change is a CANDIDATE (provisional): a newly-created skill, or a
  fresh revision (optimize / absorb). It is kept ("入库") only when a later round
  carrying it gets an effective reviewer verdict (confirm); a candidate that
  carries a FAILED round is discarded — a fresh skill deleted, a revision
  reverted to its last-confirmed snapshot. The judge is the reviewer's verdict on
  the ROUND, never the skill text.
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
        author_model="memory",
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

def test_skill_gap_optimizes_matched_skill(tmp_path: Path) -> None:
    """failure + matched (confirmed) skill + reviewer skill_gap lesson -> the
    author OPTIMIZES the skill (folds the lesson in). The revision becomes a
    CANDIDATE (provisional) that must re-prove."""
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
        config=SkillLoopConfig(max_rounds=1,
                               skill_revise_on_failure=True),
        on_event=events.append,
    )
    outcome = loop.run("say hi to the user", workdir=tmp_path)

    assert outcome.status == "blocked", outcome
    assert any(e.get("type") == "skill.optimized" for e in events), [
        e.get("type") for e in events
    ]
    revise_calls = [p for label, p, _ in backend.history
                    if label == "distiller.revise.failure_lesson"]
    assert len(revise_calls) == 1
    store = SkillStore(skills_dir)
    summary = next(s for s in store.list_summaries()
                   if s["name"] == "Write a hello message")
    revised = store.load(summary["path"])
    assert "Failure lessons" in revised.render()
    # The revision is a candidate (unproven) until a later round confirms it.
    assert revised.provisional is True


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
        config=SkillLoopConfig(max_rounds=1,
                               skill_revise_on_failure=True),
        on_event=events.append,
    )
    loop.run("say hi to the user", workdir=tmp_path)

    assert not any(e.get("type", "").startswith("skill.lesson") for e in events)
    assert not any(label == "distiller.revise.failure_lesson"
                   for label, _, _ in backend.history)


def test_unproven_optimization_reverted_on_next_failure(tmp_path: Path) -> None:
    """An OPTIMIZE makes the skill a candidate. If the next round carrying that
    revision also fails, the revision did not prove out -> revert to the last
    confirmed version (no quality-of-text judgment, only effect)."""
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)

    # Run 1: matched confirmed skill fails -> author optimizes it (candidate).
    b1 = MemoryBackend()
    b1.queue("matcher", _match_hello())
    b1.queue("engineer-r1", CannedResponse(message="Ran a shell tool."))
    b1.queue("reviewer", CannedResponse(message=_blocked_review()))
    b1.queue("distiller.revise.failure_lesson",
             CannedResponse(message=REVISED_SKILL_MD))
    ev1: list[dict] = []
    SkillLoop(
        skills_dir=skills_dir, engineer_runner=b1, reviewer_runner=b1,
        config=SkillLoopConfig(max_rounds=1,
                               skill_revise_on_failure=True),
        on_event=ev1.append,
    ).run("say hi to the user", workdir=tmp_path)
    assert any(e.get("type") == "skill.optimized" for e in ev1)
    store = SkillStore(skills_dir)
    s1 = next(s for s in store.list_summaries() if s["name"] == "Write a hello message")
    assert store.load(s1["path"]).provisional is True

    # Run 2: the now-provisional revision is carried into a round that ALSO fails
    # -> it did not prove out -> revert to the last confirmed version.
    b2 = MemoryBackend()
    b2.queue("matcher", _match_hello())
    b2.queue("engineer-r1", CannedResponse(message="Ran a shell tool again."))
    b2.queue("reviewer", CannedResponse(message=_blocked_review()))
    ev2: list[dict] = []
    SkillLoop(
        skills_dir=skills_dir, engineer_runner=b2, reviewer_runner=b2,
        config=SkillLoopConfig(max_rounds=1,
                               skill_revise_on_failure=True),
        on_event=ev2.append,
    ).run("say hi to the user", workdir=tmp_path)

    assert any(e.get("type") == "skill.candidate.dropped" for e in ev2), [
        e.get("type") for e in ev2
    ]
    fresh = SkillStore(skills_dir)
    s2 = next(s for s in fresh.list_summaries() if s["name"] == "Write a hello message")
    reverted = fresh.load(s2["path"])
    assert reverted.provisional is False          # reverted to confirmed
    assert "Failure lessons" not in reverted.render()  # the bad revision is gone


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
        config=SkillLoopConfig(max_rounds=1),
        on_event=events.append,
    )
    loop.run("say hi to the user", workdir=tmp_path)

    assert not any(e.get("type", "").startswith("skill.lesson") for e in events)
    assert not any(label == "distiller.revise.failure_lesson"
                   for label, _, _ in backend.history)


# ---------------------------------------------------------------------------
# Provisional skill lifecycle
# ---------------------------------------------------------------------------

def test_skill_gap_creates_candidate_when_unmatched(tmp_path: Path) -> None:
    """No skill matched + the reviewer attributes a fixable ``skill_gap`` ->
    AUTHOR the missing skill as a CANDIDATE (provisional). This reviewer-gated
    creation REPLACES the old proactive distill-on-miss: a skill is born only
    after the reviewer diagnoses a real gap, never just because the matcher
    missed (the old behaviour minted a throwaway playbook for every trivial
    task)."""
    skills_dir = tmp_path / "skills"

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("engineer-r1", CannedResponse(message="Ran a shell tool."))
    backend.queue("reviewer", CannedResponse(message=_blocked_review()))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))  # reviewer-gated authoring

    events: list[dict] = []
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1, skill_revise_on_failure=True),
        on_event=events.append,
    )
    loop.run("say hi to the user", workdir=tmp_path)

    assert any(e.get("type") == "skill.created" for e in events), [
        e.get("type") for e in events
    ]
    store = SkillStore(skills_dir)
    created = next((s for s in store.list_summaries()
                    if s["name"] == "Write a hello message"), None)
    assert created is not None, "reviewer skill_gap must author the missing skill"
    # Born a candidate — must prove effective on a later round to be kept.
    assert store.load(created["path"]).provisional is True


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
        config=SkillLoopConfig(max_rounds=1,
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


def test_provisional_skill_discarded_on_reuse_failure(tmp_path: Path) -> None:
    """A candidate (provisional) skill with no prior confirmed version that is
    carried into a failed round did not prove out -> it is discarded immediately
    (no archive-after-N tolerance) and no longer appears as a candidate."""
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir, provisional=True)

    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="Ran a shell tool."))
    backend.queue("reviewer", CannedResponse(message=_blocked_review()))

    events: list[dict] = []
    loop = SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1,
                               skill_revise_on_failure=True),
        on_event=events.append,
    )
    loop.run("say hi to the user", workdir=tmp_path)

    assert any(e.get("type") == "skill.candidate.dropped" for e in events), [
        e.get("type") for e in events
    ]
    fresh = SkillStore(skills_dir)
    assert not any(s["name"] == "Write a hello message"
                   for s in fresh.list_summaries())


# ---------------------------------------------------------------------------
# Store-level provisional unit coverage
# ---------------------------------------------------------------------------

def test_skill_provisional_field_round_trips() -> None:
    from argus_skill.skills.store import Skill

    skill = Skill(name="x", description="d", category="c", content="# body\nok",
                  provisional=True)
    assert Skill.parse(skill.render()).provisional is True

    # Legacy frontmatter (no provisional key) parses as confirmed.
    legacy = Skill(name="y", description="d", category="c", content="# body\nok")
    assert Skill.parse(legacy.render()).provisional is False


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


def test_revision_snapshots_prev_then_confirm_clears_it(tmp_path: Path) -> None:
    """OPTIMIZE/ABSORB snapshots the last-confirmed version to a .prev sidecar so
    an ineffective revision can be reverted; confirming the candidate clears it."""
    from argus_skill.skills.skill_author import Distiller

    store = _seed_skill(tmp_path / "skills")  # confirmed
    summary = next(s for s in store.list_summaries()
                   if s["name"] == "Write a hello message")
    skill = store.load(summary["path"])
    assert skill.provisional is False

    backend = MemoryBackend()
    backend.queue("distiller.revise.failure_lesson",
                  CannedResponse(message=REVISED_SKILL_MD))
    assert store.promote_lesson(
        skill=skill, lesson_text=_LESSON, task_description="say hi",
        distiller=Distiller(backend), author_model="memory") is True

    snap = Path(skill.path).parent / f".{Path(skill.path).stem}.prev.md"
    assert store.load(skill.path).provisional is True   # revision is a candidate
    assert snap.exists()                                # snapshot taken
    assert "Failure lessons" not in snap.read_text(encoding="utf-8")  # holds the original

    assert store.confirm_provisional(store.load(skill.path)) is True
    assert not snap.exists()                            # confirm drops the snapshot


def test_optimization_rejected_when_author_declines(tmp_path: Path) -> None:
    """If the author returns unusable content, the optimization is rejected and
    the matched skill is left UNTOUCHED (no candidate, no snapshot)."""
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)

    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="Ran a shell tool."))
    backend.queue("reviewer", CannedResponse(message=_blocked_review()))
    backend.queue("distiller.revise.failure_lesson", CannedResponse(message=""))

    events: list[dict] = []
    SkillLoop(
        skills_dir=skills_dir, engineer_runner=backend, reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1,
                               skill_revise_on_failure=True),
        on_event=events.append,
    ).run("say hi to the user", workdir=tmp_path)

    assert any(e.get("type") == "skill.optimize.rejected" for e in events), [
        e.get("type") for e in events
    ]
    store = SkillStore(skills_dir)
    summary = next(s for s in store.list_summaries()
                   if s["name"] == "Write a hello message")
    skill = store.load(summary["path"])
    assert skill.provisional is False                       # untouched
    assert "Failure lessons" not in skill.render()
    snap = Path(skill.path).parent / f".{Path(skill.path).stem}.prev.md"
    assert not snap.exists()                                # no snapshot on rejection
