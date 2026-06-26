"""The shipped systemd unit (deploy/argus-skill.service) is a real, coherent
unit — not a README snippet that rots. Guards the 7×24-durability contract:
crash-restart + a drain ExecStop that matches the actual CLI.
"""
from __future__ import annotations

import configparser
from pathlib import Path

_UNIT = Path(__file__).resolve().parents[1] / "deploy" / "argus-skill.service"


def _parse() -> configparser.ConfigParser:
    # systemd units are INI-ish; allow duplicate-free section parse.
    cp = configparser.ConfigParser(strict=False)
    text = "\n".join(
        ln for ln in _UNIT.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )
    cp.read_string(text)
    return cp


def test_unit_file_is_shipped() -> None:
    assert _UNIT.is_file(), f"missing shipped systemd unit at {_UNIT}"


def test_unit_has_crash_restart() -> None:
    cp = _parse()
    assert cp.get("Service", "Restart") == "on-failure"


def test_unit_stops_via_drain_not_sigkill() -> None:
    # ExecStop MUST use the drain stop so systemd never interrupts a mission.
    cp = _parse()
    assert "--daemon-stop --drain" in cp.get("Service", "ExecStop")
    # And give the drain real time before systemd escalates to SIGKILL.
    assert int(cp.get("Service", "TimeoutStopSec")) >= 600


def test_unit_starts_in_foreground_daemon_mode() -> None:
    cp = _parse()
    assert "--daemon-fg" in cp.get("Service", "ExecStart")
