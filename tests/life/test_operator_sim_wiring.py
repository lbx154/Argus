"""The simulated operator wiring is OPT-IN via env flag (default OFF)."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.life import operator_sim


def test_disabled_by_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ARGUS_SKILL_SIMULATED_OPERATOR", raising=False)
    assert operator_sim.simulated_operator_enabled() is False
    provider = operator_sim.operator_guidance_provider_from_env(
        project_root=tmp_path, objective="x", runner=None
    )
    assert provider is None


def test_flag_falsey_values_keep_it_off(monkeypatch, tmp_path: Path) -> None:
    for val in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("ARGUS_SKILL_SIMULATED_OPERATOR", val)
        assert operator_sim.simulated_operator_enabled() is False
        assert (
            operator_sim.operator_guidance_provider_from_env(
                project_root=tmp_path, objective="x", runner=None
            )
            is None
        )


def test_flag_on_wires_a_provider(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_SIMULATED_OPERATOR", "1")
    monkeypatch.setenv("ARGUS_SKILL_SIMULATED_OPERATOR_SEED", "7")
    assert operator_sim.simulated_operator_enabled() is True
    provider = operator_sim.operator_guidance_provider_from_env(
        project_root=tmp_path, objective="optimize within budget", runner=None
    )
    assert provider is not None
    out = provider()
    assert isinstance(out, list) and len(out) == 1 and out[0].strip()


def test_seed_makes_provider_deterministic(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_SIMULATED_OPERATOR", "yes")
    monkeypatch.setenv("ARGUS_SKILL_SIMULATED_OPERATOR_SEED", "42")
    p1 = operator_sim.operator_guidance_provider_from_env(
        project_root=tmp_path, objective="obj", runner=None
    )
    p2 = operator_sim.operator_guidance_provider_from_env(
        project_root=tmp_path, objective="obj", runner=None
    )
    assert p1 is not None and p2 is not None
    # Same seed -> identical first message.
    assert p1() == p2()


def test_env_provider_threads_on_event_and_grounding(
    monkeypatch, tmp_path: Path
) -> None:
    """The env gate forwards on_event + trace_path + checkpoint_path through."""
    monkeypatch.setenv("ARGUS_SKILL_SIMULATED_OPERATOR", "1")
    monkeypatch.setenv("ARGUS_SKILL_SIMULATED_OPERATOR_SEED", "5")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    trace = state_dir / "events.jsonl"
    trace.write_text('{"text":"VAL_SIGNAL=0.73"}', encoding="utf-8")
    checkpoint = state_dir / "checkpoint.json"
    checkpoint.write_text(
        json.dumps({"tried_and_failed": ["the obvious knob"], "next_step": "measure"}),
        encoding="utf-8",
    )

    events: list[dict] = []
    provider = operator_sim.operator_guidance_provider_from_env(
        project_root=tmp_path,
        objective="optimize within budget",
        runner=None,
        trace_path=trace,
        checkpoint_path=checkpoint,
        on_event=events.append,
    )
    assert provider is not None
    out = provider()
    assert isinstance(out, list) and len(out) == 1 and out[0].strip()
    # The intervention is observable.
    assert len(events) == 1
    assert events[0]["type"] == "life.operator_sim"
    assert events[0]["message"] == out[0]
    assert events[0]["band"] in {
        operator_sim.BAND_HEAD,
        operator_sim.BAND_MID,
        operator_sim.BAND_TAIL,
    }


def test_env_provider_off_ignores_grounding_kwargs(monkeypatch, tmp_path: Path) -> None:
    # With the flag OFF the gate returns None even when grounding kwargs are passed.
    monkeypatch.delenv("ARGUS_SKILL_SIMULATED_OPERATOR", raising=False)
    provider = operator_sim.operator_guidance_provider_from_env(
        project_root=tmp_path,
        objective="x",
        runner=None,
        trace_path=tmp_path / "events.jsonl",
        checkpoint_path=tmp_path / "checkpoint.json",
        on_event=lambda _ev: None,
    )
    assert provider is None
