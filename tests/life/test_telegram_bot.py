from __future__ import annotations

from types import SimpleNamespace

from argus_skill.life.telegram_bot import _CommandRouter


def test_detect_active_layer_requires_explicit_agent_layer() -> None:
    router = _CommandRouter.__new__(_CommandRouter)
    mem = SimpleNamespace(
        journal=SimpleNamespace(
            tail=lambda n: [
                SimpleNamespace(kind="planner_done", extra={}),
                SimpleNamespace(kind="mission_started", extra={}),
            ]
        )
    )

    assert router._detect_active_layer(mem) == ""


def test_detect_active_layer_reads_explicit_agent_layer() -> None:
    router = _CommandRouter.__new__(_CommandRouter)
    mem = SimpleNamespace(
        journal=SimpleNamespace(
            tail=lambda n: [
                SimpleNamespace(
                    kind="mission_started",
                    extra={"agent_layer": "engineer"},
                ),
            ]
        )
    )

    assert router._detect_active_layer(mem) == "👷 工程师 (L1)"
