"""Reviewer-proposed skill memory: skill_ops → SkillRouter → Manager gate.

The new contract:

* The reviewer never mutates skills directly. It emits ``skill_ops`` in its
  verdict — ``create``/``update`` PROPOSALS (each carrying playbook markdown)
  and ``archive``/``delete`` requests.
* ``SkillRouter`` owns the write path. A create/update must clear, in order:
  (1) mechanical structure, (2) independence (not a near-duplicate), and
  (3) the Manager generality+correctness gate (an LLM judge on the reviewer
  backend, run_label ``manager.skill_review``). ``archive`` is applied directly.
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


def _approve(why: str = "general + correct") -> CannedResponse:
    return CannedResponse(message=json.dumps({"approve": True, "why": why}))


def _reject(why: str = "only fits this task") -> CannedResponse:
    return CannedResponse(message=json.dumps({"approve": False, "why": why}))


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


# ---------------------------------------------------------------------------
# create — gated by the Manager
# ---------------------------------------------------------------------------

def test_reviewer_create_approved_by_manager(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("engineer-r1", CannedResponse(message="Ran a shell tool."))
    backend.queue("reviewer", CannedResponse(
        message=_review_with_ops(skill_ops=[{"op": "create", "content": SKILL_MD,
                                             "why": "reusable greeting capability"}])))
    backend.queue("manager.skill_review", _approve())

    events: list[dict] = []
    _loop(skills_dir, backend, events).run("say hi to the user", workdir=tmp_path)

    assert any(e.get("type") == "skill.created" for e in events), [e.get("type") for e in events]
    store = SkillStore(skills_dir)
    created = next((s for s in store.list_summaries()
                    if s["name"] == "Write a hello message"), None)
    assert created is not None
    assert store.load(created["path"]).provisional is True


def test_reviewer_create_rejected_by_manager(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("engineer-r1", CannedResponse(message="Ran a shell tool."))
    backend.queue("reviewer", CannedResponse(
        message=_review_with_ops(skill_ops=[{"op": "create", "content": SKILL_MD,
                                             "why": "x"}])))
    backend.queue("manager.skill_review", _reject())

    events: list[dict] = []
    _loop(skills_dir, backend, events).run("say hi to the user", workdir=tmp_path)

    assert any(e.get("type") == "skill.proposal.rejected" for e in events), [
        e.get("type") for e in events]
    assert not SkillStore(skills_dir).list_summaries()


def test_create_rejected_when_malformed_before_manager(tmp_path: Path) -> None:
    """A one-liner proposal fails the mechanical check and never reaches the
    Manager (no manager.skill_review call queued — would raise if reached)."""
    skills_dir = tmp_path / "skills"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("engineer-r1", CannedResponse(message="ran"))
    backend.queue("reviewer", CannedResponse(
        message=_review_with_ops(skill_ops=[{"op": "create", "content": "## Title\nx",
                                             "why": "too short"}])))

    events: list[dict] = []
    _loop(skills_dir, backend, events).run("say hi", workdir=tmp_path)

    assert any(e.get("type") == "skill.proposal.rejected" for e in events)
    assert not SkillStore(skills_dir).list_summaries()


def test_create_rejected_when_too_similar(tmp_path: Path) -> None:
    """A near-duplicate of an existing skill fails independence before the
    Manager (no manager call queued)."""
    skills_dir = tmp_path / "skills"
    _seed_skill(skills_dir)  # existing "Write a hello message"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("engineer-r1", CannedResponse(message="ran"))
    backend.queue("reviewer", CannedResponse(
        message=_review_with_ops(skill_ops=[{"op": "create", "content": SKILL_MD,
                                             "why": "dup"}])))

    events: list[dict] = []
    _loop(skills_dir, backend, events).run("say hi", workdir=tmp_path)

    rejected = [e for e in events if e.get("type") == "skill.proposal.rejected"]
    assert rejected and "similar" in rejected[0].get("text", "")
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
    outcome = _loop(skills_dir, backend, events).run("say hi", workdir=tmp_path)

    assert outcome.status == "done"
    assert any(e.get("type") == "skill.confirmed" for e in events)
    store = SkillStore(skills_dir)
    s = next(x for x in store.list_summaries() if x["name"] == "Write a hello message")
    assert store.load(s["path"]).provisional is False


def test_skill_ops_ignored_when_disabled(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("engineer-r1", CannedResponse(message="ran"))
    backend.queue("reviewer", CannedResponse(
        message=_review_with_ops(skill_ops=[{"op": "create", "content": SKILL_MD, "why": "x"}])))

    events: list[dict] = []
    _loop(skills_dir, backend, events, enabled=False).run("say hi", workdir=tmp_path)

    assert not any(e.get("type") in ("skill.created", "skill.proposal.rejected") for e in events)
    assert not SkillStore(skills_dir).list_summaries()
