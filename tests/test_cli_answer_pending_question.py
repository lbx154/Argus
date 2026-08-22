"""An unattended box has to be able to answer the question that stopped it.

A mission that pauses to ask something sits at ``paused_operator`` until the
question is answered. Until `--answer` existed the only channel that cleared
that was the web cockpit, so a daemon could sit blocked for hours on "may I
install torch?" while `--notify` — which only leaves guidance for the next
round — appeared to do something and did not.

Clearing the question and resuming the SAME item is the trap: the mission
re-reads the objective that made it ask and asks again. The answer has to
arrive as a continuation whose objective carries it as authority.
"""

from __future__ import annotations

import argparse

import pytest

from argus_skill.apps.cli import _core
from argus_skill.life.memory import Backlog, BacklogItem


def _backlog(tmp_path):
    return Backlog(tmp_path / "backlog.jsonl")


def _paused(backlog, item_id: str, question: str) -> None:
    backlog.add(
        BacklogItem(
            id=item_id,
            ts=0.0,
            title=f"mission {item_id}",
            objective="work",
        )
    )
    backlog.update(item_id, status="paused_operator", pending_question=question)


def _args(_tmp_path, **over):
    fields = {"answer": None, "answer_item": ""}
    fields.update(over)
    return argparse.Namespace(**fields)


@pytest.fixture
def _project(tmp_path, monkeypatch):
    class _Bundle:
        class project:
            root = tmp_path

    monkeypatch.setattr(_core, "_resolve_project_bundle", lambda args: _Bundle())
    return tmp_path


def test_the_answer_reaches_the_round_that_runs_next(_project) -> None:
    """The mission that asked must not simply run again and re-ask."""
    backlog = _backlog(_project)
    _paused(backlog, "abc123", "May I create a venv and install torch?")

    code = _core._cmd_answer(_args(_project, answer="Yes, go ahead."))

    assert code == 0
    asked = next(i for i in backlog.all() if i.id == "abc123")
    assert not asked.pending_question

    runnable = [i for i in backlog.all() if i.status == "pending"]
    assert len(runnable) == 1
    assert runnable[0].id != "abc123", "resuming the asker re-asks the question"
    assert "Yes, go ahead." in runnable[0].objective


def test_answering_twice_does_not_enqueue_the_work_twice(_project) -> None:
    backlog = _backlog(_project)
    _paused(backlog, "abc123", "May I install torch?")

    assert _core._cmd_answer(_args(_project, answer="Yes.")) == 0
    assert _core._cmd_answer(_args(_project, answer="Yes.")) == 1
    assert len([i for i in backlog.all() if i.status == "pending"]) == 1


def test_empty_answers_are_refused(_project) -> None:
    assert _core._cmd_answer(_args(_project, answer="   ")) == 2


def test_nothing_waiting_is_reported_rather_than_guessed(_project) -> None:
    _backlog(_project)
    assert _core._cmd_answer(_args(_project, answer="ok")) == 1


def test_several_waiting_missions_must_be_disambiguated(_project) -> None:
    backlog = _backlog(_project)
    _paused(backlog, "aaa", "question one?")
    _paused(backlog, "bbb", "question two?")

    assert _core._cmd_answer(_args(_project, answer="ok")) == 2

    assert _core._cmd_answer(_args(_project, answer="ok", answer_item="bbb")) == 0
    by_id = {i.id: i for i in backlog.all()}
    assert not by_id["bbb"].pending_question
    assert by_id["aaa"].status == "paused_operator"
    assert by_id["aaa"].pending_question == "question one?"


def test_an_unknown_item_is_refused(_project) -> None:
    backlog = _backlog(_project)
    _paused(backlog, "aaa", "question?")

    assert _core._cmd_answer(_args(_project, answer="ok", answer_item="zzz")) == 1
    assert next(i for i in backlog.all() if i.id == "aaa").status == "paused_operator"


def test_answering_does_not_need_a_terminal() -> None:
    """The whole point is unblocking an unattended box, so the launcher must
    route --answer to the Python CLI rather than treating it as a request to
    open the cockpit — which fails without a tty."""
    from argus_skill.apps.tui_launcher import _uses_python_admin

    assert _uses_python_admin(["--answer", "yes"])
    assert _uses_python_admin(["--answer=yes"])
    assert _uses_python_admin(["--answer", "yes", "--answer-item", "abc123"])
    # A bare launch is still the cockpit.
    assert not _uses_python_admin([])
