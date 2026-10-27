"""An unattended box has to be able to answer the question that stopped it.

A mission that pauses to ask something sits at ``paused_operator`` until the
question is answered. Until `--answer` existed the only channel that cleared
that was the web cockpit, so a daemon could sit blocked for hours on "may I
install torch?" while `--notify` — which only leaves guidance for the next
round — appeared to do something and did not.

Clearing the question and resuming the item as it was is the trap: the
mission re-reads the objective that made it ask and asks again. The task
resumes with the answer in its objective, as authority.
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from argus.apps.cli import _core
from argus.life.memory import Backlog, BacklogItem


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
    backlog.update(item_id, park_reason="operator_pause", pending_question=question)


def _args(_tmp_path, **over):
    fields = {"answer": None, "answer_item": ""}
    fields.update(over)
    return argparse.Namespace(**fields)


@pytest.fixture
def _project(tmp_path, monkeypatch):
    life = tmp_path / "projects" / "s-answer"
    life.mkdir(parents=True)
    bundle = SimpleNamespace(project=SimpleNamespace(root=life, fingerprint="s-answer"), global_root=tmp_path)
    monkeypatch.setattr(_core, "_resolve_project_bundle", lambda args: bundle)
    monkeypatch.setattr("argus.webapi.daemon_lifecycle.start_project_daemon", lambda *_a, **_k: {"rc": 0})
    return life


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
    assert by_id["aaa"].status == "parked"
    assert by_id["aaa"].pending_question == "question one?"


def test_an_unknown_item_is_refused(_project) -> None:
    backlog = _backlog(_project)
    _paused(backlog, "aaa", "question?")

    assert _core._cmd_answer(_args(_project, answer="ok", answer_item="zzz")) == 1
    assert next(i for i in backlog.all() if i.id == "aaa").status == "parked"


def test_framework_deployment_answer_uses_the_decision_boundary(
    tmp_path, monkeypatch,
) -> None:
    class _Project:
        root = tmp_path / "project"
        fingerprint = "session-1"

    class _Bundle:
        project = _Project()
        global_root = tmp_path

    backlog = _backlog(_Project.root)
    _paused(backlog, "maintenance-1", "Adopt the reviewed framework change?")
    backlog.update(
        "maintenance-1",
        operator_decision={
            "id": "decision-1",
            "status": "pending",
            "decision_kind": "framework_deployment",
            "options": [{"id": "adopt", "label": "Adopt"}, {"id": "decline", "label": "Decline"}],
        },
    )
    observed = {}

    def resolve(sid, decision_id, option_id, **kwargs):
        observed.update(
            sid=sid,
            decision_id=decision_id,
            option_id=option_id,
            global_root=kwargs["global_root"],
        )
        return {"resolved": True, "reply": "Deployment completed."}

    monkeypatch.setattr(_core, "_resolve_project_bundle", lambda args: _Bundle())
    monkeypatch.setattr(
        "argus.webapi.manager_pending_question.manager_resolve_operator_decision",
        resolve,
    )

    assert _core._cmd_answer(_args(tmp_path, answer="adopt")) == 0
    assert observed == {
        "sid": "session-1",
        "decision_id": "decision-1",
        "option_id": "adopt",
        "global_root": tmp_path,
    }
    assert len(backlog.all()) == 1
