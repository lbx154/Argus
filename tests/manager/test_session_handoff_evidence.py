from __future__ import annotations

import json

from argus.core.models import RunnerOptions, RunnerResult
from argus.daemon.state import write_continuous_config
from argus.life.memory import Backlog, BacklogItem
from argus.manager._session_ops import _ManagerSession
from argus.manager.observation import observe_project
from argus.manager.session_context import remember_turn
from argus.manager.supervision import _prompt


def test_rotated_manager_gets_observed_changes_instead_of_supervision_boilerplate(tmp_path):
    write_continuous_config(tmp_path, enabled=True, objective="Preserve grouped-means acceptance")
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = BacklogItem.new(title="Group validation", objective="Validate independent means")
    item.acceptance_check = "Means must be 2 and 12; exclude missing values"
    backlog.add(item)

    class Backend:
        backend = "pi"

        def __init__(self):
            self.calls = []

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            self.calls.append((prompt, resume_thread_id))
            return RunnerResult(exit_code=0, agent_messages=["The grouped-means evidence was checked."],
                                thread_id=resume_thread_id or f"thread-{len(self.calls)}")

    backend = Backend()
    for number in range(6):
        event = {"type": "round.review.completed", "item_id": item.id,
                 "reason": f"EVIDENCE_CHANGE_{number}: independent result for this observation"}
        observation = observe_project(tmp_path, event=event)
        _ManagerSession(backend, tmp_path).run_exec(prompt=_prompt(observation),
            options=RunnerOptions(model="fixture/one"), run_label="manager-supervision")
    stored = json.loads((tmp_path / ".manager_session.json").read_text())
    turns = stored["recent_turns"]
    assert len(turns) == 4
    for number, turn in zip(range(2, 6), turns, strict=True):
        assert f"EVIDENCE_CHANGE_{number}" in turn["request_excerpt"]
        assert "Preserve grouped-means acceptance" in turn["request_excerpt"]
        assert "Means must be 2 and 12" in turn["request_excerpt"]
        assert "Choose CONTINUE" not in turn["request_excerpt"]
        assert len(turn["request_excerpt"]) <= 2000 and len(turn["answer_excerpt"]) <= 3000
    assert backend.calls[0][1] is None
    assert all(call[1] == "thread-1" for call in backend.calls[1:])
    _ManagerSession(backend, tmp_path).run_exec(prompt="Message:\nWhat changed?", options=RunnerOptions(model="fixture/two"), run_label="manager-ask")
    assert backend.calls[-1][1] is None  # Only explicit model change rotates.
    assert "EVIDENCE_CHANGE_5" in backend.calls[-1][0]
    assert "Means must be 2 and 12" in backend.calls[-1][0]
    assert "Message:\nWhat changed?" in backend.calls[-1][0]


def test_continuity_redacts_before_a_credential_crosses_the_excerpt_cutoff(monkeypatch):
    secret = "sk-" + "privatefixture" * 6
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    result = RunnerResult(exit_code=0, agent_messages=["a" * 2992 + secret])
    turns = remember_turn({}, "Message:\n" + "q" * 1992 + secret, result, "manager-ask")
    assert secret[:8] not in turns[0]["request_excerpt"]
    assert secret[:8] not in turns[0]["answer_excerpt"]
    assert len(turns[0]["request_excerpt"]) <= 2000
    assert len(turns[0]["answer_excerpt"]) <= 3000
