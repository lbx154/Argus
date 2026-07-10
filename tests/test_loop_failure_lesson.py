"""Reviewer-proposed skill memory: skill_ops → SkillRouter.

The contract:

* The reviewer never mutates skills directly. It emits ``skill_ops`` in its
  verdict — ``create``/``update`` PROPOSALS (each carrying playbook markdown)
  and ``archive``/``delete`` requests.
* ``SkillRouter`` owns the write path. A create/update must clear, in order:
  (1) mechanical structure, (2) independence (not a near-duplicate). There is
  NO Manager approval gate — the Reviewer is the sole authority.
  ``archive``/``delete`` are applied directly (the reviewer's direct
  authority; a protected/governing skill is refused).
* Every stored change is a CANDIDATE (provisional) and is only confirmed when a
  later round carrying it is effective.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig, SkillStore
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend

# A well-formed CAPABILITY playbook (passes the mechanical structure check:
# a title, a description, a When-to-use and a How-to-solve, > 120 chars).
SKILL_MD = (
    "## Title\nWrite a hello message\n\n"
    "## Description\nGenerate a friendly greeting for any user-facing context.\n\n"
    "## Category\nhello\n\n"
    "## When to use\n- user asks to say hi or greet someone\n"
    "- the live objective is purely conversational and no work is required\n\n"
    "## When NOT to use\n- user wants production code or files modified\n\n"
    "## How to solve\n- Read the task and identify the desired tone.\n"
    "- Compose a one-line greeting that answers without filler.\n\n"
    "## Examples\n- 'say hi' → reply with 'hello world'\n"
    "\n## Sources\n"
    "- [Python documentation](https://docs.python.org/3/) — deterministic text handling.\n"
    "- [Unicode Standard](https://www.unicode.org/standard/standard.html) — user-facing text.\n"
)


def _done_review() -> str:
    return json.dumps({
        "status": "done",
        "reason": "Greeting produced.", "next_action": "None.",
        "round_summary_markdown": "# Review\n\n- done\n",
        "completion_summary_markdown": "Done.",
    })


def _review_with_ops(*, status: str = "blocked", skill_ops: list[dict]) -> str:
    return json.dumps({
        "status": status,
        "reason": "verdict with skill ops",
        "next_action": "carry the lesson forward",
        "round_summary_markdown": "# Review\n\n- proposed skill ops\n",
        "completion_summary_markdown": "",
        "skill_ops": skill_ops,
    })


def _match_hello() -> CannedResponse:
    return CannedResponse(message=json.dumps({
        "matched": [{"name": "Write a hello message", "fit": "high", "why": "greeting"}],
    }))


def _seed_skill(skills_dir: Path, *, provisional: bool = False) -> SkillStore:
    store = SkillStore(skills_dir)
    store.save_distilled(
        task_description="say hi to the user",
        raw_distill_output=SKILL_MD,
        provisional=provisional,
    )
    return store


def _loop(skills_dir: Path, backend: MemoryBackend, events: list,
          *, enabled: bool = True) -> SkillLoop:
    return SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1, skill_ops_enabled=enabled),
        on_event=events.append,
    )


def _queue_no_op_distill(backend: MemoryBackend) -> None:
    """Suppress the SEPARATE Scientist auto-distill path (fires whenever the
    matcher returns ``"matched": []``, independent of skill_ops): without a
    queued response the distiller call gets ``MemoryBackend``'s default
    filler text and ``save_distilled`` mis-parses it into a garbage
    "unnamed-skill" entry, polluting these tests' skill-count assertions.
    ``NONE`` is the Scientist's own documented "no reusable pattern" output."""
    backend.queue("scientist.skill_distill", CannedResponse(message="NONE"))


# ---------------------------------------------------------------------------
# create — mechanical + independence checks (no Manager gate)
# ---------------------------------------------------------------------------

def test_reviewer_create_is_stored_as_provisional_no_manager_gate(tmp_path: Path) -> None:
    """A well-formed, non-duplicate create clears mechanical + independence and
    is stored directly — there is no Manager judge call in between."""
    skills_dir = tmp_path / "skills"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    _queue_no_op_distill(backend)
    backend.queue("engineer-r1", CannedResponse(message="Ran a shell tool."))
    backend.queue("reviewer", CannedResponse(
        message=_review_with_ops(skill_ops=[{"op": "create", "content": SKILL_MD,
                                             "why": "reusable greeting capability"}])))

    events: list[dict] = []
    _loop(skills_dir, backend, events).run("say hi to the user", workdir=tmp_path)

    assert any(e.get("type") == "skill.created" for e in events), [e.get("type") for e in events]
    reviewer_options = next(
        options for label, _prompt, options in backend.history if label == "reviewer"
    )
    assert reviewer_options.live_search is True
    store = SkillStore(skills_dir)
    created = next((s for s in store.list_summaries()
                    if s["name"] == "Write a hello message"), None)
    assert created is not None
    assert store.load(created["path"]).provisional is True


def test_create_rejected_when_malformed(tmp_path: Path) -> None:
    """A one-liner proposal fails the mechanical structure check."""
    skills_dir = tmp_path / "skills"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    _queue_no_op_distill(backend)
    backend.queue("engineer-r1", CannedResponse(message="ran"))
    backend.queue("reviewer", CannedResponse(
        message=_review_with_ops(skill_ops=[{"op": "create", "content": "## Title\nx",
                                             "why": "too short"}])))

    events: list[dict] = []
    _loop(skills_dir, backend, events).run("say hi", workdir=tmp_path)

    assert any(e.get("type") == "skill.proposal.rejected" for e in events)
    assert not SkillStore(skills_dir).list_summaries()


def test_create_rejected_when_too_similar(tmp_path: Path) -> None:
    """A near-duplicate of an existing skill fails the independence check.

    Independence is judged ENTIRELY by an LLM (no lexical/scored fallback),
    so this queues a duplicate-check verdict for the judge (the reviewer
    backend, wired as SkillRouter's ``judge_runner``)."""
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)  # existing "Write a hello message"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    _queue_no_op_distill(backend)
    backend.queue("engineer-r1", CannedResponse(message="ran"))
    backend.queue("reviewer", CannedResponse(
        message=_review_with_ops(skill_ops=[{"op": "create", "content": SKILL_MD,
                                             "why": "dup"}])))
    backend.queue("skill.duplicate_check", CannedResponse(message=json.dumps({
        "duplicate": True, "of": "Write a hello message", "why": "identical content",
    })))

    events: list[dict] = []
    _loop(skills_dir, backend, events).run("say hi", workdir=tmp_path)

    rejected = [e for e in events if e.get("type") == "skill.proposal.rejected"]
    assert rejected and "name already exists" in rejected[0].get("text", "")
    # still exactly the one seeded skill
    assert len(SkillStore(skills_dir).list_summaries()) == 1


# ---------------------------------------------------------------------------
# archive — reviewer's direct authority (no Manager gate)
# ---------------------------------------------------------------------------

def test_reviewer_archive_retires_skill(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)
    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="done"))
    backend.queue("reviewer", CannedResponse(
        message=_review_with_ops(status="done", skill_ops=[
            {"op": "archive", "name": "Write a hello message", "why": "wrong/harmful"}])))

    events: list[dict] = []
    _loop(skills_dir, backend, events).run("say hi", workdir=tmp_path)

    assert any(e.get("type") == "skill.archived" for e in events), [e.get("type") for e in events]
    assert not any(s["name"] == "Write a hello message"
                   for s in SkillStore(skills_dir).list_summaries())


# ---------------------------------------------------------------------------
# provisional lifecycle (unchanged) + disabled switch
# ---------------------------------------------------------------------------

def test_provisional_confirmed_on_successful_reuse(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir, provisional=True)
    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="hello world"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    loop = _loop(skills_dir, backend, events)
    outcome = loop.run("greet Alice", workdir=tmp_path)

    assert outcome.status == "done"
    assert any(e.get("type") == "skill.confirmed" for e in events)
    store = SkillStore(skills_dir)
    s = next(x for x in store.list_summaries() if x["name"] == "Write a hello message")
    learned = store.load(s["path"])
    assert learned.provisional is False
    assert learned.successful_reuses == 1


def _continue_review() -> str:
    return json.dumps({
        "status": "continue",
        "reason": "still working",
        "next_action": "keep going",
        "round_summary_markdown": "# Review\n\n- continue\n",
        "completion_summary_markdown": "",
    })


def _blocked_review() -> str:
    return json.dumps({
        "status": "blocked",
        "reason": "blocked on GPU quota (external, not the skill's fault)",
        "next_action": "wait for quota",
        "round_summary_markdown": "# Review\n\n- blocked\n",
        "completion_summary_markdown": "",
    })


def test_provisional_discarded_when_mission_ineffective(tmp_path: Path) -> None:
    """Root-cause fix for the dead ``discard_provisional`` wire: a matched
    provisional candidate that dragged through an INEFFECTIVE mission
    (``max_rounds`` here) is closed out — discarded (fresh skill) rather than
    lingering provisional where an unrelated later success could confirm it."""
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir, provisional=True)
    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="still trying"))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))

    events: list[dict] = []
    outcome = _loop(skills_dir, backend, events).run("say hi", workdir=tmp_path)

    assert outcome.status == "max_rounds"
    assert any(e.get("type") == "skill.discarded" for e in events), [
        e.get("type") for e in events]
    # the fresh candidate is archived out of the active library
    assert not any(s["name"] == "Write a hello message"
                   for s in SkillStore(skills_dir).list_summaries())


def test_provisional_kept_when_mission_blocked(tmp_path: Path) -> None:
    """A provisional candidate is NOT punished for an EXTERNAL abort: a mission
    that ends ``blocked`` (GPU quota, backend down, ...) leaves the candidate
    provisional so it can still prove itself on a later, unblocked mission."""
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir, provisional=True)
    backend = MemoryBackend()
    backend.queue("matcher", _match_hello())
    backend.queue("engineer-r1", CannedResponse(message="attempted"))
    backend.queue("reviewer", CannedResponse(message=_blocked_review()))

    events: list[dict] = []
    outcome = _loop(skills_dir, backend, events).run("say hi", workdir=tmp_path)

    assert outcome.status == "blocked"
    assert not any(e.get("type") in ("skill.discarded", "skill.reverted")
                   for e in events), [e.get("type") for e in events]
    store = SkillStore(skills_dir)
    s = next(x for x in store.list_summaries() if x["name"] == "Write a hello message")
    assert store.load(s["path"]).provisional is True


def test_skill_ops_ignored_when_disabled(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    _queue_no_op_distill(backend)
    backend.queue("engineer-r1", CannedResponse(message="ran"))
    backend.queue("reviewer", CannedResponse(
        message=_review_with_ops(skill_ops=[{"op": "create", "content": SKILL_MD, "why": "x"}])))

    events: list[dict] = []
    _loop(skills_dir, backend, events, enabled=False).run("say hi", workdir=tmp_path)

    assert not any(e.get("type") in ("skill.created", "skill.proposal.rejected") for e in events)
    assert not SkillStore(skills_dir).list_summaries()
