from __future__ import annotations

from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.webapi.protocol import API_CAPABILITIES
from argus_skill.webapi.server import abort_project_mission


def test_abort_endpoint_helper_targets_running_item(tmp_path) -> None:
    life = tmp_path / "projects" / "s-abort"
    life.mkdir(parents=True)
    backlog = LifeMemory.open(life).backlog
    item = backlog.add(BacklogItem.new(title="task", objective="work"))
    backlog.mark_running(item.id)

    result = abort_project_mission(
        "s-abort",
        reason="operator stop",
        global_root=tmp_path,
    )

    assert result == {
        "requested": True,
        "item_id": item.id,
        "message": f"Stop requested for running task {item.id}.",
    }
    assert (life / "mission_abort_request.json").exists()


def test_abort_endpoint_helper_is_idle_safe(tmp_path) -> None:
    life = tmp_path / "projects" / "s-idle"
    life.mkdir(parents=True)

    result = abort_project_mission("s-idle", global_root=tmp_path)

    assert result == {
        "requested": False,
        "item_id": None,
        "message": "No running task to abort. Pending tasks were left unchanged.",
    }
    assert not (life / "mission_abort_request.json").exists()


def test_abort_is_a_declared_webapi_capability() -> None:
    assert "mission.abort.v1" in API_CAPABILITIES
