"""Characterization of the TEAM dispatch contract.

These tests pin the OBSERVABLE behaviour of operator TEAM dispatch so that
removing the vestigial front-door "lifetime" surface can be shown to be a
no-op. They describe what the code does today; they are deliberately written
against the seam an operator request actually travels through, not against the
helper that is being removed.

Frozen contract:

1. Every TEAM request becomes a continuous (durable) campaign.
2. The front-door lifetime hint never changes that outcome — including when it
   explicitly says ``bounded``.
3. Dispatch spends no model call deciding lifetime.

If a future change intends to reintroduce a finite TEAM lifetime, these tests
are the ones that must be rewritten first, on purpose.
"""

from __future__ import annotations

from types import SimpleNamespace

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


@pytest.fixture(autouse=True)
def manager_runner(monkeypatch):
    class Manager:
        def decide_vertical(self, body, **kwargs):
            return SimpleNamespace(execution_task=f"managed: {body}")

        def commit_vertical_decision(self, body, decision, **kwargs):
            return SimpleNamespace(execution_task=decision.execution_task)

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda state, mem: SimpleNamespace(manager=Manager()),
    )


@pytest.mark.parametrize(
    "seeded_state",
    [
        pytest.param({}, id="no-lifetime-hint"),
        pytest.param({"_frontdoor_lifetime": "standing"}, id="hint-standing"),
        pytest.param({"_frontdoor_lifetime": "bounded"}, id="hint-bounded"),
        pytest.param({"_frontdoor_lifetime": "garbage"}, id="hint-unrecognised"),
    ],
)
def test_every_team_request_becomes_a_durable_campaign(memory, seeded_state):
    """Whatever the front door hinted, TEAM work ends up continuous."""
    state = {"backend": "codex", **seeded_state}

    assert dispatch.maybe_promote_to_continuous(memory, "do the work", state) is True
    assert state["config"]["continuous"] is True
    # The hint is consumed and discarded — it is not carried into dispatch.
    assert "_frontdoor_lifetime" not in state


def test_dispatch_uses_the_continuous_handoff_not_the_bounded_one(memory, monkeypatch):
    """The bounded branch of ``enqueue_mission`` is not on the TEAM path."""
    used: list[str] = []

    def _continuous(*args, **kwargs):
        used.append("continuous")
        return "managed objective"

    def _bounded(*args, **kwargs):
        used.append("bounded")
        raise AssertionError("TEAM dispatch must not take the bounded branch")

    monkeypatch.setattr(front_door, "manager_continuous_handoff", _continuous)
    monkeypatch.setattr(front_door, "manager_bounded_handoff", _bounded)

    state = {"backend": "codex", "_frontdoor_lifetime": "bounded"}
    dispatch.maybe_promote_to_continuous(memory, "one report", state)
    dispatch.enqueue_mission(memory, "one report", state)

    assert used == ["continuous"]


def test_dispatch_spends_no_model_call_on_lifetime(memory, monkeypatch):
    """Promotion is unconditional, so it must not pay for a classifier turn."""
    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("TEAM dispatch must not run a lifetime classifier")
        ),
    )

    state = {"backend": "codex"}
    assert dispatch.maybe_promote_to_continuous(memory, "keep optimising", state)
    assert state["config"]["continuous"] is True


def test_an_existing_campaign_is_reused_rather_than_replaced(memory):
    """A second TEAM request joins the live campaign instead of restarting it."""
    from argus_skill.daemon.life_worker import write_continuous_config

    life_dir = front_door._life_dir_for(memory)
    write_continuous_config(life_dir, enabled=True, objective="existing campaign")

    state = {"backend": "codex"}
    assert dispatch.maybe_promote_to_continuous(memory, "more work", state) is True
    assert state["continuous_objective"] == "existing campaign"
    # No fresh Manager division is pending: the campaign already has one.
    assert "_continuous_pending_manager_handoff" not in state
