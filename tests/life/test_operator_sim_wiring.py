"""The simulated operator wiring is OPT-IN via env flag (default OFF)."""
from __future__ import annotations

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
