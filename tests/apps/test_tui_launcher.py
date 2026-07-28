from pathlib import Path

import pytest

from argus_skill.apps import tui_launcher


@pytest.fixture(autouse=True)
def _trusted_special_prompt(monkeypatch) -> None:
    monkeypatch.setattr(
        "argus_skill.life.special_prompts.describe_special_prompt_gate",
        lambda: (True, ""),
    )


def test_launcher_execs_node_with_bundled_ink(monkeypatch, tmp_path: Path) -> None:
    bundle = tmp_path / "argus.mjs"
    bundle.write_text("// bundle", encoding="utf-8")
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    backend = venv_bin / "argus-skill"
    backend.write_text("#!/bin/sh\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(tui_launcher.sys, "executable", str(venv_bin / "python"))
    monkeypatch.delenv("ARGUS_SKILL_BIN", raising=False)
    monkeypatch.setattr(tui_launcher, "_bundle_path", lambda: bundle)
    monkeypatch.setattr(
        tui_launcher,
        "_tui_local_identity",
        lambda: {
            "release_id": "0.1.1+local",
            "runtime_source_digest": "abc123",
        },
    )
    monkeypatch.delenv("ARGUS_TUI_LOCAL_RELEASE_ID", raising=False)
    monkeypatch.delenv("ARGUS_TUI_LOCAL_SOURCE_DIGEST", raising=False)
    monkeypatch.setattr(tui_launcher.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(tui_launcher, "_node_major", lambda node: 20)
    monkeypatch.setattr(
        tui_launcher.os,
        "execv",
        lambda executable, argv: seen.update(executable=executable, argv=argv),
    )

    assert tui_launcher.main(["--project", "wiki"]) == 0
    assert seen["executable"] == "/usr/bin/node"
    assert seen["argv"] == ["/usr/bin/node", str(bundle), "--project", "wiki"]
    assert tui_launcher.os.environ["ARGUS_SKILL_BIN"] == str(backend)
    assert tui_launcher.os.environ["ARGUS_TUI_LOCAL_RELEASE_ID"] == "0.1.1+local"
    assert tui_launcher.os.environ["ARGUS_TUI_LOCAL_SOURCE_DIGEST"] == "abc123"


def test_launcher_clears_stale_source_digest_for_wheel_install(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_TUI_LOCAL_SOURCE_DIGEST", "stale")
    monkeypatch.setattr(
        tui_launcher,
        "_tui_local_identity",
        lambda: {
            "release_id": "0.1.1+wheel",
            "runtime_source_digest": None,
        },
    )

    tui_launcher._export_tui_local_identity()

    assert tui_launcher.os.environ["ARGUS_TUI_LOCAL_RELEASE_ID"] == "0.1.1+wheel"
    assert "ARGUS_TUI_LOCAL_SOURCE_DIGEST" not in tui_launcher.os.environ


def test_binary_launcher_points_tui_at_real_frozen_backend(
    monkeypatch, tmp_path: Path
) -> None:
    bundle = tmp_path / "argus.mjs"
    bundle.write_text("// bundle", encoding="utf-8")
    seen = {}
    monkeypatch.setenv("ARGUS_BINARY_DISTRIBUTION", "1")
    monkeypatch.setenv("ARGUS_BINARY_MODE", "tui")
    monkeypatch.delenv("ARGUS_SKILL_BIN", raising=False)
    monkeypatch.setattr(tui_launcher.sys, "executable", "/opt/argus/argus-core")
    monkeypatch.setattr(tui_launcher, "_bundle_path", lambda: bundle)
    monkeypatch.setattr(tui_launcher.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(tui_launcher, "_node_major", lambda node: 22)
    monkeypatch.setattr(
        tui_launcher.os,
        "execv",
        lambda executable, argv: seen.update(executable=executable, argv=argv),
    )

    assert tui_launcher.main([]) == 0
    assert seen["executable"] == "/usr/bin/node"
    assert tui_launcher.os.environ["ARGUS_SKILL_BIN"] == "/opt/argus/argus-core"
    assert tui_launcher.os.environ["ARGUS_BINARY_MODE"] == "cli"


def test_launcher_rejects_missing_special_prompt(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "argus_skill.life.special_prompts.describe_special_prompt_gate",
        lambda: (False, "trusted special prompt required"),
    )
    monkeypatch.setattr(
        tui_launcher,
        "_bundle_path",
        lambda: (_ for _ in ()).throw(AssertionError("TUI must not launch")),
    )

    assert tui_launcher.main([]) == 2
    assert "trusted special prompt required" in capsys.readouterr().err


def test_launcher_fails_cleanly_without_bundle(monkeypatch, capsys) -> None:
    monkeypatch.setattr(tui_launcher, "_bundle_path", lambda: None)
    assert tui_launcher.main([]) == 2
    assert "bundled Ink TUI is missing" in capsys.readouterr().err


def test_launcher_rejects_unsupported_node(monkeypatch, tmp_path: Path, capsys) -> None:
    bundle = tmp_path / "argus.mjs"
    bundle.write_text("// bundle", encoding="utf-8")
    monkeypatch.setattr(tui_launcher, "_bundle_path", lambda: bundle)
    monkeypatch.setattr(tui_launcher.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(tui_launcher, "_node_major", lambda node: 16)

    assert tui_launcher.main([]) == 2
    assert "found 16" in capsys.readouterr().err


def test_public_admin_flags_stay_on_python_admin_path(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(
        tui_launcher,
        "_run_python_admin",
        lambda argv: seen.append(argv) or 7,
    )
    monkeypatch.setattr(
        tui_launcher,
        "_bundle_path",
        lambda: (_ for _ in ()).throw(AssertionError("TUI must not launch")),
    )
    assert tui_launcher.main(["--setup", "--non-interactive"]) == 7
    assert seen == [["--setup", "--non-interactive"]]


def test_admin_subcommands_stay_on_python_admin_path(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(
        tui_launcher,
        "_run_python_admin",
        lambda argv: seen.append(argv) or 7,
    )
    monkeypatch.setattr(
        tui_launcher,
        "_bundle_path",
        lambda: (_ for _ in ()).throw(AssertionError("TUI must not launch")),
    )
    assert tui_launcher.main(["wiki", "init", "demo"]) == 7
    assert seen == [["wiki", "init", "demo"]]


def test_admin_flags_after_global_options_stay_on_python_admin_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    seen = []
    life_dir = tmp_path / "life"
    monkeypatch.setattr(
        tui_launcher,
        "_run_python_admin",
        lambda argv: seen.append(argv) or 7,
    )
    monkeypatch.setattr(
        tui_launcher,
        "_bundle_path",
        lambda: (_ for _ in ()).throw(AssertionError("TUI must not launch")),
    )

    assert tui_launcher.main(["--life-dir", str(life_dir), "--status"]) == 7
    assert seen == [["--life-dir", str(life_dir), "--status"]]


def test_admin_flags_after_capability_options_stay_on_python_admin_path(
    monkeypatch,
) -> None:
    seen = []
    monkeypatch.setattr(
        tui_launcher,
        "_run_python_admin",
        lambda argv: seen.append(argv) or 7,
    )
    monkeypatch.setattr(
        tui_launcher,
        "_bundle_path",
        lambda: (_ for _ in ()).throw(AssertionError("TUI must not launch")),
    )

    assert tui_launcher.main(["--backend", "codex", "--auth-mode", "model_api", "--doctor"]) == 7
    assert seen == [["--backend", "codex", "--auth-mode", "model_api", "--doctor"]]
