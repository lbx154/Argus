from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi import daemon_lifecycle, server
from argus.webapi.routes.daemon import _resume_parks_after_start


@pytest.mark.parametrize("rc,enabled,resumed", [(0, True, True), (1, True, False), (0, False, False)])
def test_explicit_resume_ends_every_park_the_planner_or_a_question_does_not_hold(
    tmp_path, rc, enabled, resumed,
):
    memory = LifeMemory.open(tmp_path)
    for reason in ["provider_fence", "operator_pause", "infra", "research_open", "operator_question"]:
        item = memory.backlog.add(BacklogItem.new(title=reason, objective="synthetic"))
        memory.backlog.update(
            item.id, park_reason=reason,
            pending_question="Which?" if reason == "operator_question" else "",
        )
    result = {"rc": rc}
    assert _resume_parks_after_start(tmp_path, result, enabled=enabled) is result
    rows = {item.title: item.status for item in memory.backlog.all()}
    for reason in ["provider_fence", "operator_pause", "infra"]:
        assert rows[reason] == ("pending" if resumed else "parked")
    # The Planner retires its own parks; only the answer ends a question.
    assert rows["research_open"] == rows["operator_question"] == "parked"


def test_authenticated_idempotent_start_is_the_only_resume_action(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_WORKBENCH_HOST_ROOT", str(tmp_path))
    sid = "s-explicit-fence"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    memory = LifeMemory.open(life)
    item = BacklogItem.new(title="held", objective="synthetic")
    item.status = "paused_provider_fence"
    memory.backlog.add(item)
    calls = []
    def start(*args, **kwargs):
        calls.append(1)
        return {"rc": 0, "sid": sid}
    monkeypatch.setattr(daemon_lifecycle, 'start_project_daemon', start)
    client = TestClient(server.create_app(global_root=tmp_path, auth_token="fixture"))
    route = f"/api/projects/{sid}/daemon/start"
    assert client.post(route).status_code == 401
    assert not calls and memory.backlog.all()[0].status == "parked"
    body = {"command_id": "explicit-fence-resume"}
    headers = {"Authorization": "Bearer fixture"}
    assert client.post(route, json=body, headers=headers).status_code == 200
    assert client.post(route, json=body, headers=headers).status_code == 200
    assert calls == [1]
    row = memory.backlog.all()[0]
    assert row.status == "pending" and row.attempt == 2
