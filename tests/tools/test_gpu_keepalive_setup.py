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
    assert body.startswith("---\nscope: paper\n---")
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


def test_setup_creates_trusted_default_house_rules(tmp_path: Path, monkeypatch) -> None:
    sp_dir = tmp_path / "special_prompts"
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(sp_dir))
    from argus_skill.life import special_prompts

    path = _wizard._ensure_default_house_rules_prompt()

    assert path == sp_dir / "10-house-rules.md"
    assert path is not None
    assert (path.stat().st_mode & 0o777) == 0o644
    assert "unrelated jobs" in path.read_text(encoding="utf-8")
    assert special_prompts.describe_special_prompt_gate() == (True, "")


def test_setup_preserves_existing_trusted_house_rules(
    tmp_path: Path, monkeypatch
) -> None:
    sp_dir = tmp_path / "special_prompts"
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(sp_dir))
    custom = _wizard._write_special_prompt(
        "05-operator-policy.md", "Keep operator-authored policy unchanged.\n"
    )

    assert _wizard._ensure_default_house_rules_prompt() is None
    assert custom.read_text(encoding="utf-8") == "Keep operator-authored policy unchanged.\n"
    assert list(sp_dir.glob("*.md")) == [custom]


def test_setup_does_not_overwrite_untrusted_house_rules(
    tmp_path: Path, monkeypatch
) -> None:
    sp_dir = tmp_path / "special_prompts"
    sp_dir.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(sp_dir))
    existing = sp_dir / "10-house-rules.md"
    existing.write_text("Operator draft; do not overwrite.\n", encoding="utf-8")
    existing.chmod(0o666)

    generated = _wizard._ensure_default_house_rules_prompt()

    assert generated is not None
    assert generated == sp_dir / "10-house-rules-setup-1.md"
    assert existing.read_text(encoding="utf-8") == "Operator draft; do not overwrite.\n"
    assert (generated.stat().st_mode & 0o022) == 0
    from argus_skill.life import special_prompts

    assert special_prompts.describe_special_prompt_gate() == (True, "")


def test_gpu_load_script_is_bundled() -> None:
    p = _wizard._gpu_load_script_path()
    assert p.name == "gpu_load.py"
    assert p.exists()


def test_gpu_load_help_exits_clean() -> None:
    with pytest.raises(SystemExit) as exc:
        gpu_load.main(["--help"])
    assert exc.value.code == 0


def test_save_and_load_author_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    path = _wizard._save_author("lbx154", "lbxhaixing154@sjtu.edu.cn")
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    loaded = _wizard._load_existing_author()
    assert loaded == {"name": "lbx154", "email": "lbxhaixing154@sjtu.edu.cn"}


def test_configure_author_prompts_and_sets_git(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    answers = iter(["lbx154", "lbxhaixing154@sjtu.edu.cn"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    applied: dict[str, str] = {}
    monkeypatch.setattr(_wizard, "_git_global_identity", lambda: ("", ""))
    monkeypatch.setattr(
        _wizard, "_apply_git_identity",
        lambda name, email: applied.update(name=name, email=email) or True,
    )
    result = _wizard._configure_author(None)
    assert result == {"name": "lbx154", "email": "lbxhaixing154@sjtu.edu.cn"}
    assert applied == {"name": "lbx154", "email": "lbxhaixing154@sjtu.edu.cn"}
    assert _wizard._load_existing_author() == result


def test_configure_author_skip_when_blank(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    monkeypatch.setattr(_wizard, "_git_global_identity", lambda: ("", ""))
    monkeypatch.setattr(_wizard, "_apply_git_identity", lambda *a: True)
    assert _wizard._configure_author(None) is None
    assert _wizard._load_existing_author() is None


def test_configure_author_defaults_from_existing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # blank answers -> keep the supplied defaults
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    monkeypatch.setattr(_wizard, "_git_global_identity", lambda: ("", ""))
    monkeypatch.setattr(_wizard, "_apply_git_identity", lambda *a: True)
    existing = {"name": "lbx154", "email": "lbxhaixing154@sjtu.edu.cn"}
    result = _wizard._configure_author(existing)
    assert result == existing


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
    assert body.startswith("---\nscope: paper\n---")
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
