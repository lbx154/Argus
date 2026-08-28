import os
from pathlib import Path

import pytest

from argus_skill.apps import tui_launcher


class _Stdin:
    """A stand-in for ``sys.stdin`` whose tty-ness the test chooses."""

    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture(autouse=True)
def _interactive_stdin(monkeypatch):
    """The launcher is invoked from a terminal; pytest's stdin is not one.

    `main()` refuses to start the cockpit without a tty, so every test that
    exercises a later step has to look interactive. Tests about the refusal
    itself override this.
    """
    monkeypatch.delenv("ARGUS_SKILL_ALLOW_HEADLESS_TUI", raising=False)
    monkeypatch.setattr(tui_launcher.sys, "stdin", _Stdin(tty=True))


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
    monkeypatch.setattr(tui_launcher, "_node_version", lambda node: (22, 12, 0))
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
    monkeypatch.setattr(tui_launcher, "_node_version", lambda node: (22, 12, 0))
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


def test_launcher_without_special_prompt_proceeds_to_bundle_check(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setenv(
        "ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(tmp_path / "empty_special")
    )
    monkeypatch.setattr(tui_launcher, "_bundle_path", lambda: None)

    assert tui_launcher.main([]) == 2
    assert "bundled Ink TUI is missing" in capsys.readouterr().err


def test_launcher_fails_cleanly_without_bundle(monkeypatch, capsys) -> None:
    monkeypatch.setattr(tui_launcher, "_bundle_path", lambda: None)
    assert tui_launcher.main([]) == 2
    assert "bundled Ink TUI is missing" in capsys.readouterr().err


def test_launcher_rejects_unsupported_node(monkeypatch, tmp_path: Path, capsys) -> None:
    bundle = tmp_path / "argus.mjs"
    bundle.write_text("// bundle", encoding="utf-8")
    monkeypatch.setattr(tui_launcher, "_bundle_path", lambda: bundle)
    monkeypatch.setattr(tui_launcher.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(tui_launcher, "_node_version", lambda node: (22, 11, 0))

    assert tui_launcher.main([]) == 2
    assert "found 22.11.0" in capsys.readouterr().err


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


def test_ask_stays_on_python_admin_path(monkeypatch) -> None:
    """`--ask` is a headless admin surface; it must never launch the cockpit."""
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

    assert tui_launcher.main(["--ask", "what is 2+2?"]) == 7
    assert seen == [["--ask", "what is 2+2?"]]


def test_web_launch_uses_tui_unless_raw_backend_options_are_requested(
    monkeypatch, tmp_path: Path,
) -> None:
    bundle = tmp_path / "argus.mjs"
    bundle.write_text("// bundle", encoding="utf-8")
    seen = {}
    admin = []
    monkeypatch.setattr(tui_launcher, "_bundle_path", lambda: bundle)
    monkeypatch.setattr(tui_launcher.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(tui_launcher, "_node_version", lambda node: (22, 12, 0))
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
    assert tui_launcher.main(["--web", "--host", "127.0.0.1", "--port", "8801"]) == 7
    assert admin == [
        ["--web", "--web-port", "8800"],
        ["--web", "--host", "127.0.0.1", "--port", "8801"],
    ]


def test_documented_web_aliases_before_action_stay_on_python_admin_path(
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
    argv = ["--host", "127.0.0.1", "--port", "8801", "--web"]

    assert tui_launcher.main(argv) == 7
    assert seen == [argv]


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
    monkeypatch.setattr(tui_launcher, "_node_version", lambda node: (22, 12, 0))
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


def test_launcher_refuses_a_cockpit_without_a_terminal(monkeypatch, capsys) -> None:
    """Ink puts stdin in raw mode, so no terminal means no cockpit.

    A piped, redirected or cron-launched `argus` used to announce that it was
    starting the backend and then die inside the bundle with a JavaScript
    stack trace and a link to Ink's README. Nothing may start, and the reply
    must name the surfaces that do work without a terminal.
    """
    monkeypatch.delenv("ARGUS_SKILL_ALLOW_HEADLESS_TUI", raising=False)
    monkeypatch.setattr(tui_launcher.sys, "stdin", _Stdin(tty=False))
    monkeypatch.setattr(
        tui_launcher,
        "_bundle_path",
        lambda: (_ for _ in ()).throw(AssertionError("TUI must not launch")),
    )

    assert tui_launcher.main([]) == 2
    err = capsys.readouterr().err
    assert "needs an interactive terminal" in err
    for surface in ("--web", "--watch", "--status", "--daemon"):
        assert surface in err
    assert "Raw mode" not in err


def test_launcher_keeps_the_cockpit_when_stdin_is_a_terminal(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_ALLOW_HEADLESS_TUI", raising=False)
    monkeypatch.setattr(tui_launcher.sys, "stdin", _Stdin(tty=True))
    assert tui_launcher._headless_stdin_error() == ""


def test_a_headless_cockpit_can_be_forced_for_an_embedding_host(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_ALLOW_HEADLESS_TUI", "1")
    monkeypatch.setattr(tui_launcher.sys, "stdin", _Stdin(tty=False))
    assert tui_launcher._headless_stdin_error() == ""


def test_admin_commands_still_run_without_a_terminal(monkeypatch) -> None:
    """The gate guards the cockpit only; `--status` and friends stay headless."""
    monkeypatch.delenv("ARGUS_SKILL_ALLOW_HEADLESS_TUI", raising=False)
    monkeypatch.setattr(tui_launcher.sys, "stdin", _Stdin(tty=False))
    monkeypatch.setattr(tui_launcher, "_run_python_admin", lambda argv: 0)

    assert tui_launcher.main(["--status"]) == 0
