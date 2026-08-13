import os
from pathlib import Path

import pytest

from argus_skill.apps import tui_launcher


class _ReconfigurableStream:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def reconfigure(self, **kwargs: str) -> None:
        self.calls.append(dict(kwargs))


def test_windows_console_streams_are_forced_to_utf8(monkeypatch) -> None:
    stdout = _ReconfigurableStream()
    stderr = _ReconfigurableStream()
    monkeypatch.setattr(tui_launcher.sys, "stdout", stdout)
    monkeypatch.setattr(tui_launcher.sys, "stderr", stderr)

    tui_launcher._configure_windows_console_encoding(platform_name="nt")

    expected = [{"encoding": "utf-8", "errors": "replace"}]
    assert stdout.calls == expected
    assert stderr.calls == expected


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
    backend = venv_bin / ("argus-skill.exe" if os.name == "nt" else "argus-skill")
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
    monkeypatch.setattr(tui_launcher, "_needs_foreground_spawn", lambda: False)
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
    monkeypatch.setattr(tui_launcher, "_needs_foreground_spawn", lambda: False)
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
    assert tui_launcher.main(["--pair-plan"]) == 7
    assert tui_launcher.main(["--daemon-stop", "--resume", "s-holder"]) == 7
    assert seen == [
        ["--setup", "--non-interactive"],
        ["--pair-plan"],
        ["--daemon-stop", "--resume", "s-holder"],
    ]


def test_web_launch_uses_tui_unless_raw_backend_options_are_requested(
    monkeypatch, tmp_path: Path,
) -> None:
    bundle = tmp_path / "argus.mjs"
    bundle.write_text("// bundle", encoding="utf-8")
    seen = {}
    admin = []
    monkeypatch.setattr(tui_launcher, "_bundle_path", lambda: bundle)
    monkeypatch.setattr(tui_launcher.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(tui_launcher, "_node_major", lambda node: 20)
    monkeypatch.setattr(tui_launcher, "_needs_foreground_spawn", lambda: False)
    monkeypatch.setattr(
        tui_launcher.os,
        "execv",
        lambda executable, argv: seen.update(executable=executable, argv=argv),
    )
    monkeypatch.setattr(
        tui_launcher,
        "_run_python_admin",
        lambda argv: admin.append(argv) or 7,
    )

    assert tui_launcher.main(["--web", "--no-open"]) == 0
    assert seen["argv"] == ["/usr/bin/node", str(bundle), "--web", "--no-open"]
    assert tui_launcher.main(["--web", "--web-port", "8800"]) == 7
    assert admin == [["--web", "--web-port", "8800"]]


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
    assert tui_launcher.main(["update"]) == 7
    assert tui_launcher.main(["--update"]) == 7
    assert seen == [["wiki", "init", "demo"], ["update"], ["--update"]]


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


@pytest.mark.parametrize("spelling", ["separate", "equals"])
def test_interactive_life_dir_configures_tui_state_root(
    monkeypatch,
    tmp_path: Path,
    spelling: str,
) -> None:
    bundle = tmp_path / "argus.mjs"
    bundle.write_text("// bundle", encoding="utf-8")
    life_dir = tmp_path / "state"
    seen = {}
    monkeypatch.delenv("ARGUS_SKILL_HOME", raising=False)
    monkeypatch.setattr(tui_launcher, "_bundle_path", lambda: bundle)
    monkeypatch.setattr(tui_launcher.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(tui_launcher, "_node_major", lambda node: 20)
    monkeypatch.setattr(tui_launcher, "_needs_foreground_spawn", lambda: False)
    monkeypatch.setattr(
        tui_launcher.os,
        "execv",
        lambda executable, argv: seen.update(executable=executable, argv=argv),
    )
    argv = (
        ["--life-dir", str(life_dir), "--project", "s-demo"]
        if spelling == "separate"
        else [f"--life-dir={life_dir}", "--project", "s-demo"]
    )

    assert tui_launcher.main(argv) == 0

    assert os.environ["ARGUS_SKILL_HOME"] == str(life_dir.resolve())
    assert seen["argv"] == ["/usr/bin/node", str(bundle), "--project", "s-demo"]


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
