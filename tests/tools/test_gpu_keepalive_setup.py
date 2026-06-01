"""Tests for the setup wizard's GPU keep-alive (anti-reclaim) integration."""
from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.tools import gpu_lease, gpu_load
from argus_skill.tools import setup as _wizard


def test_build_keepalive_config_shape() -> None:
    cfg = _wizard._build_keepalive_config(
        "/opt/py/bin/python", Path("/abs/argus_skill/tools/gpu_load.py"),
        [0, 1], util=20.0, mem=10.0,
    )
    cmd = cfg["command"]
    assert cmd[0] == "/opt/py/bin/python"
    assert cmd[1].endswith("gpu_load.py")
    assert "--gpus" in cmd and "0,1" in cmd
    assert "--mem" in cmd and "10.0" in cmd
    assert "--util" in cmd and "20.0" in cmd
    # match token is the precise inert marker, NOT the broad basename
    assert cfg["match"] == _wizard._KEEPALIVE_TOKEN
    assert cfg["match"] != "gpu_load.py"
    assert _wizard._KEEPALIVE_TOKEN in cmd
    assert cfg["devices"] == [0, 1]


def test_render_prompt_mentions_lease_protocol_and_devices() -> None:
    body = _wizard._render_gpu_keepalive_prompt("0,1,2,3")
    assert "0,1,2,3" in body
    assert "gpu_lease run" in body
    assert "park" in body
    # must not encourage killing the loader by hand
    assert "DON'T `kill`" in body or "DON'T kill" in body


def test_save_keepalive_is_readable_by_gpu_lease(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_GPU_KEEPALIVE_CONFIG", raising=False)
    cfg = _wizard._build_keepalive_config(
        "python", _wizard._gpu_load_script_path(), [0], util=20.0, mem=10.0,
    )
    path = _wizard._save_gpu_keepalive(cfg)
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    loaded = gpu_lease.load_config()
    assert loaded["match"] == _wizard._KEEPALIVE_TOKEN
    assert loaded["command"][1].endswith("gpu_load.py")


def test_special_prompt_passes_trust_check(tmp_path: Path, monkeypatch) -> None:
    sp_dir = tmp_path / "special_prompts"
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(sp_dir))
    # import inside test so it picks up the env-driven directory
    from argus_skill.life import special_prompts

    body = _wizard._render_gpu_keepalive_prompt("0,1")
    path = _wizard._write_special_prompt("20-gpu-keepalive.md", body)
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o644
    # not group/world-writable -> accepted by the trust check
    assert (path.stat().st_mode & 0o022) == 0
    loaded = dict(special_prompts.load_special_prompts())
    assert "20-gpu-keepalive" in loaded


def test_gpu_load_script_is_bundled() -> None:
    p = _wizard._gpu_load_script_path()
    assert p.name == "gpu_load.py"
    assert p.exists()


def test_gpu_load_help_exits_clean() -> None:
    with pytest.raises(SystemExit) as exc:
        gpu_load.main(["--help"])
    assert exc.value.code == 0


def test_gpu_load_arg_defaults() -> None:
    args = gpu_load._parse_args([])
    assert args.util == 20.0
    assert args.mem == 10.0
    assert args.duration == 0.0
    args2 = gpu_load._parse_args(["--gpus", "0,2", "--mem", "5", "--util", "15"])
    assert args2.gpus == "0,2"
    assert args2.mem == 5.0
    assert args2.util == 15.0


def test_experiment_api_prompt_content() -> None:
    body = _wizard._render_experiment_api_prompt()
    assert "reward" in body.lower()
    assert "judge" in body.lower()
    assert "OPENAI_API_KEY" in body
    # must not relax rigor and must forbid key leakage
    assert "anti-mediocrity" in body.lower()
    assert "Never write the API key" in body


def test_configure_experiment_api_writes_prompt(tmp_path: Path, monkeypatch) -> None:
    sp_dir = tmp_path / "special_prompts"
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(sp_dir))
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
    from argus_skill.life import special_prompts

    ok = _wizard._configure_experiment_api({"engineer": {"api_key": "sk-x"}})
    assert ok is True
    path = sp_dir / "30-experiment-api.md"
    assert path.exists()
    assert (path.stat().st_mode & 0o022) == 0  # not group/world-writable
    assert "30-experiment-api" in dict(special_prompts.load_special_prompts())


def test_configure_experiment_api_skips_without_api(tmp_path: Path, monkeypatch) -> None:
    sp_dir = tmp_path / "special_prompts"
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(sp_dir))
    # no api_key in any route -> skip, never prompt
    ok = _wizard._configure_experiment_api({"engineer": {}})
    assert ok is False
    assert not (sp_dir / "30-experiment-api.md").exists()


def test_configure_experiment_api_decline(tmp_path: Path, monkeypatch) -> None:
    sp_dir = tmp_path / "special_prompts"
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(sp_dir))
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    ok = _wizard._configure_experiment_api({"engineer": {"api_key": "sk-x"}})
    assert ok is False
    assert not (sp_dir / "30-experiment-api.md").exists()
