"""Characterization of the operator-visible TEAM lifetime contract.

The merged front-door call distinguishes an explicit bounded increment, a
finite complete outcome, and standing work, while the normal Manager decision
independently chooses direct versus staged workflow. Explicit increments and
finite direct work enter the bounded DAG path; standing or finite staged work
uses the durable campaign supervisor.
"""

from __future__ import annotations

import pytest

from argus_skill.life import MemoryBundle
from argus_skill.manager import dispatch, front_door


@pytest.fixture()
def memory(tmp_path):
    mem = MemoryBundle.for_cwd(
        tmp_path,
        global_root=tmp_path / "root",
        fingerprint="s-teamchar1",
    )
    mem.init()
    return mem


@pytest.mark.parametrize(
    ("hint", "expected_continuous"),
    [
        pytest.param(None, True, id="missing-defaults-standing"),
        pytest.param("standing", True, id="standing"),
        pytest.param("bounded", False, id="bounded"),
        pytest.param("bounded_increment", False, id="bounded-increment"),
        pytest.param("garbage", True, id="malformed-defaults-standing"),
    ],
)
def test_team_lifetime_controls_dispatch_mode(memory, hint, expected_continuous):
    state = {"backend": "codex"}
    if hint is not None:
        state["_frontdoor_lifetime"] = hint

    promoted = dispatch.maybe_promote_to_continuous(memory, "do the work", state)

    assert promoted is expected_continuous
    assert state["config"]["continuous"] is expected_continuous
    assert "_frontdoor_lifetime" not in state


def test_bounded_lifetime_spends_no_second_model_call(memory, monkeypatch):
    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("dispatch must reuse the merged front-door verdict")
        ),
    )
    state = {"backend": "codex", "_frontdoor_lifetime": "bounded"}

    assert dispatch.maybe_promote_to_continuous(memory, "one report", state) is False
    assert state["config"]["continuous"] is False


def test_standing_dispatch_uses_continuous_handoff(memory, monkeypatch):
    used: list[str] = []

    def _continuous(*args, **kwargs):
        used.append("continuous")
        return "managed objective"

    def _bounded(*args, **kwargs):
        used.append("bounded")
        raise AssertionError("standing work must not take the bounded path")

    monkeypatch.setattr(front_door, "manager_continuous_handoff", _continuous)
    monkeypatch.setattr(front_door, "manager_bounded_handoff", _bounded)
    state = {"backend": "codex", "_frontdoor_lifetime": "standing"}

    assert dispatch.maybe_promote_to_continuous(memory, "keep improving", state)
    dispatch.enqueue_mission(memory, "keep improving", state)

    assert used == ["continuous"]


def test_existing_campaign_is_reused_for_standing_work(memory):
    from argus_skill.daemon.life_worker import write_continuous_config

    life_dir = front_door._life_dir_for(memory)
    write_continuous_config(life_dir, enabled=True, objective="existing campaign")
    state = {"backend": "codex", "_frontdoor_lifetime": "standing"}

    assert dispatch.maybe_promote_to_continuous(memory, "more work", state) is True
    assert state["continuous_objective"] == "existing campaign"
    assert "_continuous_pending_manager_handoff" not in state


def test_bounded_supplement_does_not_replace_existing_campaign(memory):
    from argus_skill.daemon.life_worker import (
        read_continuous_state,
        write_continuous_config,
    )

    life_dir = front_door._life_dir_for(memory)
    write_continuous_config(life_dir, enabled=True, objective="existing campaign")
    state = {"backend": "codex", "_frontdoor_lifetime": "bounded"}

    assert dispatch.maybe_promote_to_continuous(memory, "one finite check", state) is False
    persisted = read_continuous_state(life_dir)
    assert persisted.enabled is True
    assert persisted.objective == "existing campaign"
