"""An unattended box has to be able to answer the question that stopped it.

A mission that pauses to ask something sits at ``paused_operator`` until the
question is answered. Until `--answer` existed the only channel that cleared
that was the web cockpit, so a daemon could sit blocked for hours on "may I
install torch?" while `--notify` — which only leaves guidance for the next
round — appeared to do something and did not.
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


def test_answering_clears_the_pause_and_runs_the_mission_again(_project) -> None:
    backlog = _backlog(_project)
    _paused(backlog, "abc123", "May I create a venv and install torch?")

    code = _core._cmd_answer(_args(_project, answer="Yes, go ahead."))

    assert code == 0
    item = next(i for i in backlog.all() if i.id == "abc123")
    assert item.status == "pending"
    assert not item.pending_question
    assert "Yes, go ahead." in item.notes
    assert item.attempt >= 2


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
    statuses = {i.id: i.status for i in backlog.all()}
    assert statuses["bbb"] == "pending"
    assert statuses["aaa"] == "paused_operator"


def test_an_unknown_item_is_refused(_project) -> None:
    backlog = _backlog(_project)
    _paused(backlog, "aaa", "question?")

    assert _core._cmd_answer(_args(_project, answer="ok", answer_item="zzz")) == 1
    assert next(i for i in backlog.all() if i.id == "aaa").status == "paused_operator"
