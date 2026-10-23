"""Characterization of the operator-visible TEAM dispatch contract.

There is one dispatch topology: every TEAM task runs in a Planner-run
campaign. The merged front-door call contributes one bit, whether the
campaign stops when done (BOUNDED, the default) or keeps going (STANDING).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.daemon.life_worker import read_continuous_state, write_continuous_config
from argus.life import MemoryBundle
from argus.manager import dispatch, front_door


@pytest.fixture()
def memory(tmp_path):
    mem = MemoryBundle.for_cwd(
        tmp_path,
        global_root=tmp_path / "root",
        fingerprint="s-teamchar1",
    )
    mem.init()
    return mem


@pytest.fixture(autouse=True)
def manager_runner(monkeypatch):
    class Manager:
        def decide_vertical(self, body, **kwargs):
            return SimpleNamespace(assumptions=(), execution_task=f"managed: {body}")

        def commit_vertical_decision(self, body, decision, **kwargs):
            return decision

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda state, mem: SimpleNamespace(manager=Manager()),
    )


@pytest.mark.parametrize(
    ("hint", "open_ended"),
    [
        pytest.param(None, False, id="missing-defaults-bounded"),
        pytest.param("standing", True, id="standing"),
        pytest.param("bounded", False, id="bounded"),
        pytest.param("bounded_increment", False, id="legacy-increment-is-bounded"),
        pytest.param("garbage", False, id="malformed-defaults-bounded"),
    ],
)
def test_team_lifetime_only_sets_whether_the_campaign_stops(memory, hint, open_ended):
    state = {"backend": "codex"}
    if hint is not None:
        state["_frontdoor_lifetime"] = hint

    dispatch.enqueue_mission(memory, "do the work", state)

    campaign = read_continuous_state(front_door._life_dir_for(memory))
    assert campaign.enabled is True
    assert campaign.open_ended is open_ended
    assert state["config"]["continuous"] is True
    assert "_frontdoor_lifetime" not in state


@pytest.mark.parametrize("hint", ["bounded", "standing"])
def test_every_lifetime_uses_the_campaign_handoff(memory, monkeypatch, hint):
    used: list[str] = []

    def _continuous(*args, persist, **kwargs):
        used.append("continuous")
        persist("managed objective", None)
        return "managed objective"

    def _bounded(*args, **kwargs):
        raise AssertionError("a new task must arm a campaign")

    monkeypatch.setattr(front_door, "manager_continuous_handoff", _continuous)
    monkeypatch.setattr(front_door, "manager_bounded_handoff", _bounded)
    state = {"backend": "codex", "_frontdoor_lifetime": hint}

    dispatch.enqueue_mission(memory, "keep improving", state)

    assert used == ["continuous"]


def test_standing_work_replaces_the_running_campaign(memory):
    life_dir = front_door._life_dir_for(memory)
    write_continuous_config(life_dir, enabled=True, objective="existing campaign")
    state = {"backend": "codex", "_frontdoor_lifetime": "standing"}

    dispatch.enqueue_mission(memory, "more work", state)

    assert read_continuous_state(life_dir).objective == "managed: more work"
    assert state["continuous_objective"] == "managed: more work"
