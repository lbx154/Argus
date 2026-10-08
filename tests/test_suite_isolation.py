from __future__ import annotations

import os

from argus.core import plugin_manager
from argus.trial.plugins import configure_plugins
from argus.verticals import store
from tests.conftest import _isolate_argus_state_roots


def test_trial_deployment_defaults_do_not_leak_into_the_next_test(
    tmp_path, tmp_path_factory, monkeypatch,
):
    # Use the actual configuration path that leaves deployment defaults behind.
    monkeypatch.setenv("ARGUS_CRYSTALPILOT_WORKSPACE", "")
    monkeypatch.delenv("ARGUS_PLUGINS_PREINSTALL", raising=False)
    configure_plugins(tmp_path)
    monkeypatch.setenv("ARGUS_VERTICALS_PREINSTALL", "research")
    assert plugin_manager.preinstalled_ids() == ["crystalpilot"]
    assert os.environ["ARGUS_CRYSTALPILOT_WORKSPACE"]

    _isolate_argus_state_roots.__wrapped__(tmp_path_factory, monkeypatch)
    assert plugin_manager.preinstalled_ids() == []
    assert store.preinstalled_names() == []
    assert "ARGUS_CRYSTALPILOT_WORKSPACE" not in os.environ
    assert os.environ["ARGUS_PLUGINS_PREINSTALL"] == ""
    configure_plugins(tmp_path / "ordinary-trial-start")
    assert plugin_manager.preinstalled_ids() == []

    # An explicit deployment test still opts in after the isolation fixture.
    monkeypatch.setenv("ARGUS_PLUGINS_PREINSTALL", "crystalpilot")
    monkeypatch.setenv("ARGUS_VERTICALS_PREINSTALL", "research")
    assert plugin_manager.preinstalled_ids() == ["crystalpilot"]
    assert store.preinstalled_names() == ["research"]
