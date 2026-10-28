"""The trial client asks the hosted runtime only through the ``argus.hosting`` entry point."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.core import hosting as core_hosting
from argus.trial import hosting


@pytest.fixture
def registered(monkeypatch):
    """Install ``entry_points`` answers for ``argus.hosting`` and forget the cached lookup."""
    def install(*runtimes):
        entries = [SimpleNamespace(load=lambda runtime=runtime: runtime) for runtime in runtimes]
        monkeypatch.setattr(core_hosting, "entry_points",
                            lambda *, group: entries if group == "argus.hosting" else [])
        core_hosting.hosted_runtime.cache_clear()

    yield install
    core_hosting.hosted_runtime.cache_clear()


def test_without_a_registered_runtime_the_trial_client_answers_like_no_hosting(registered):
    registered()
    assert core_hosting.hosted_runtime() is core_hosting.NO_HOSTING
    assert hosting.plugin_setup_actions() is None
    assert hosting.plugin_middleware("crystalpilot") is None
    assert hosting.daemon_launch_payload(object()) is None
    with hosting.capture_runtime_call(object(), object()):
        pass


def test_a_registered_runtime_answers_the_runtime_questions(registered):
    calls = []
    runtime = SimpleNamespace(
        plugin_setup_actions=lambda: {"health"},
        plugin_middleware=lambda name: calls.append(name) or "boundary",
        daemon_launch_payload=lambda config: {"sid": "s", "ticket": "t"},
        complete_daemon_fork=lambda handoff, *, forked=True: calls.append((handoff, forked)),
    )
    registered(runtime)
    assert core_hosting.hosted_runtime() is runtime
    assert hosting.plugin_setup_actions() == {"health"}
    assert hosting.plugin_middleware("crystalpilot") == "boundary"
    assert hosting.daemon_launch_payload(object()) == {"sid": "s", "ticket": "t"}
    hosting.complete_daemon_fork("pipe", forked=False)
    assert calls == ["crystalpilot", ("pipe", False)]
