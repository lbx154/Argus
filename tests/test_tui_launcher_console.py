"""The launcher must not hand the console to two processes at once.

On POSIX ``os.execv`` replaces the running process, so the shell has nothing
left to return to until the TUI exits. Windows has no real exec: the call
starts the child and the parent exits immediately, so PowerShell prints its
next prompt while the Ink cockpit keeps running on the same console. Both then
compete for the keyboard, the cursor, and stdout — typed characters land below
the input box, letters and digits alike.

Verified on Windows 11: with ``os.execv`` the parent's own first print never
even reached the redirected file, while ``subprocess.run`` waited 3.06s for
the child and returned its status.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus_skill.apps import tui_launcher


@pytest.fixture(autouse=True)
def _interactive_stdin(monkeypatch):
    """These tests drive the launcher as a terminal invocation.

    `main()` refuses the cockpit when stdin is not a tty, which pytest's never
    is, so the console-ownership behaviour under test is only reachable once
    stdin looks interactive.
    """
    monkeypatch.delenv("ARGUS_SKILL_ALLOW_HEADLESS_TUI", raising=False)
    monkeypatch.setattr(
        tui_launcher.sys, "stdin", SimpleNamespace(isatty=lambda: True)
    )


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    """A launcher whose preflight passes, so only the spawn path is exercised."""
    bundle = tmp_path / "argus.mjs"
    bundle.write_text("// bundle", encoding="utf-8")
    monkeypatch.setattr(tui_launcher, "_bundle_path", lambda: bundle)
    monkeypatch.setattr(tui_launcher.shutil, "which", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(
        tui_launcher,
        "_node_version",
        lambda _node: (22, 12, 0),
    )
    monkeypatch.setattr(tui_launcher, "_configure_tui_backend_bin", lambda: None)
    monkeypatch.setattr(tui_launcher, "_export_tui_local_identity", lambda: None)
    return bundle


def test_windows_runs_the_cockpit_in_the_foreground(launcher, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(tui_launcher, "_needs_foreground_spawn", lambda: True)
    monkeypatch.setattr(
        tui_launcher.subprocess,
        "run",
        lambda argv, check=False: calls.append(list(argv)) or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        tui_launcher.os,
        "execv",
        lambda *_a: pytest.fail("execv on Windows leaves two processes on one console"),
    )

    assert tui_launcher.main([]) == 0
    assert calls and calls[0][0] == "/usr/bin/node"


def test_windows_propagates_the_cockpit_exit_code(launcher, monkeypatch) -> None:
    monkeypatch.setattr(tui_launcher, "_needs_foreground_spawn", lambda: True)
    monkeypatch.setattr(
        tui_launcher.subprocess,
        "run",
        lambda argv, check=False: SimpleNamespace(returncode=3),
    )

    assert tui_launcher.main([]) == 3


def test_windows_reports_an_interrupt_rather_than_crashing(launcher, monkeypatch) -> None:
    monkeypatch.setattr(tui_launcher, "_needs_foreground_spawn", lambda: True)

    def interrupted(argv, check=False):
        raise KeyboardInterrupt

    monkeypatch.setattr(tui_launcher.subprocess, "run", interrupted)

    assert tui_launcher.main([]) == 130


def test_posix_still_replaces_the_process(launcher, monkeypatch) -> None:
    # On POSIX exec is the right call: one process, one console, no shell
    # prompt until the cockpit exits.
    replaced: list[list[str]] = []
    monkeypatch.setattr(tui_launcher, "_needs_foreground_spawn", lambda: False)
    monkeypatch.setattr(
        tui_launcher.os, "execv", lambda node, argv: replaced.append([node, *argv[1:]])
    )
    monkeypatch.setattr(
        tui_launcher.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("POSIX must exec, not spawn a second process"),
    )

    tui_launcher.main([])

    assert replaced and replaced[0][0] == "/usr/bin/node"


def test_forwarded_arguments_survive_on_both_platforms(launcher, monkeypatch) -> None:
    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(tui_launcher, "_needs_foreground_spawn", lambda: True)
    monkeypatch.setattr(
        tui_launcher.subprocess,
        "run",
        lambda argv, check=False: (
            seen.update(nt=list(argv)), SimpleNamespace(returncode=0)
        )[1],
    )

    tui_launcher.main(["--project", "s-1"])

    assert seen["nt"][-2:] == ["--project", "s-1"]
