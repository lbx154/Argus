"""Tests for the daemon-safe upgrade runbook output."""
from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from argus_skill.apps.cli._core import _cmd_daemon_runbook


def test_daemon_runbook_mentions_external_shell_and_verification(
    tmp_path: Path, capsys
) -> None:
    args = Namespace(life_dir=str(tmp_path))

    rc = _cmd_daemon_runbook(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "daemon-safe upgrade runbook" in out
    assert "external shell" in out
    assert "argus-skill --daemon-stop" in out
    assert "systemctl daemon-reload" in out
    assert "argus-skill --status" in out
