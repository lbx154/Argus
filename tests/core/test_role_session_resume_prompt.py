"""A resumed role thread is not sent the sections it already holds verbatim."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus.core.role_session import RoleSessionCapsule, elide_repeated_sections

CHECKLIST = "## Stage checklist\n" + "- keep the evaluation split fixed\n" * 30
VOICE = "## How to write\n" + "Write for the reader who was not here.\n" * 20


def _prompt(reality: str) -> str:
    return "\n\n".join([
        "## Continued Planner cycle\nYou are resuming your own session.",
        CHECKLIST,
        "## Current reality\n" + reality,
        VOICE,
    ]) + "\n"


def test_identical_long_sections_are_named_not_repeated() -> None:
    sent = elide_repeated_sections(_prompt("job 2 finished"), _prompt("job 1 running"))

    assert sent.startswith("## Continued Planner cycle")
    assert "keep the evaluation split fixed" not in sent
    assert "Write for the reader" not in sent
    assert "job 2 finished" in sent
    assert "`Stage checklist`; `How to write`" in sent


def test_changed_short_and_fenced_sections_stay() -> None:
    changed = CHECKLIST + "- also report the variance\n"
    fenced = "## Example\n```\n## not a heading\n```\n" + "x" * 500
    previous = _prompt("same") + "\n" + fenced
    current = _prompt("same").replace(CHECKLIST, changed) + "\n" + fenced

    sent = elide_repeated_sections(current, previous)

    assert "also report the variance" in sent
    assert "## Current reality\nsame" in sent, "short sections are cheaper inline"
    assert "`Example`" in sent and "## not a heading" not in sent
    assert elide_repeated_sections(_prompt("a"), "") == _prompt("a")


def _open(path: Path) -> RoleSessionCapsule:
    return RoleSessionCapsule.open(
        role="planner", policy="rolling", objective_revision="r1",
        workdir=path.parent, backend="copilot", model="m",
        checkpoint_path=None, path=path,
    )


def test_capsule_elides_only_on_the_same_resumed_thread(tmp_path: Path) -> None:
    path = tmp_path / "planner-session.json"
    first = _open(path)
    assert first.prepare(max_turns=20, max_input_tokens=0) is None
    assert first.resume_prompt(_prompt("one")) == _prompt("one")
    first.complete(SimpleNamespace(thread_id="t1"), prompt=_prompt("one"))

    second = _open(path)
    assert second.prepare(max_turns=20, max_input_tokens=0) == "t1"
    assert CHECKLIST.strip() not in second.resume_prompt(_prompt("two"))
    # The provider answered on a new thread: it never saw what was omitted.
    second.complete(SimpleNamespace(thread_id="t2"), prompt=_prompt("two"))

    third = _open(path)
    assert third.prepare(max_turns=20, max_input_tokens=0) == "t2"
    assert third.resume_prompt(_prompt("three")) == _prompt("three")
    third.complete(SimpleNamespace(thread_id="t2"), prompt=_prompt("three"))

    fourth = _open(path)
    fourth.prepare(max_turns=20, max_input_tokens=0)
    fourth.rotate("turn_limit")
    assert fourth.resume_prompt(_prompt("four")) == _prompt("four")
