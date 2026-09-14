"""``argus verticals``: headless, synchronous, exit codes 0/1/2, routed away from Node."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus.apps import tui_launcher
from argus.apps.cli import build_parser, main
from argus.core.pipeline_state import write_pipeline_state
from argus.verticals import _registry, store
from tests.verticals import fake_release as fake


@pytest.fixture(autouse=True)
def release(tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv(store.HOST_ROOT_ENV, raising=False)
    monkeypatch.delenv("ARGUS_TRIAL_HARNESS", raising=False)
    catalog = fake.build_release(tmp_path / "dist", [
        fake.spec("base_v"),
        fake.spec("child_v", requires=("base_v",), parents=("base_v",), purpose_zh="子垂直"),
    ])
    monkeypatch.setenv(store.CATALOG_ENV, str(catalog))
    _registry.refresh_vertical_plugins()
    yield catalog
    _registry.refresh_vertical_plugins()
    store._purge_modules(["argus_verticals"])


def test_parser_exposes_the_verticals_subcommands() -> None:
    parser = build_parser()
    assert parser.parse_args(["verticals", "list", "--json"]).verticals_cmd == "list"
    info = parser.parse_args(["verticals", "info", "quant"])
    assert info.command == "verticals" and info.name == "quant" and info.json is False
    assert parser.parse_args(["verticals", "install", "quant", "medical"]).names == ["quant", "medical"]
    assert parser.parse_args(["verticals", "update"]).names == []
    remove = parser.parse_args(["verticals", "remove", "quant", "--force"])
    assert remove.name == "quant" and remove.force is True
    assert parser.parse_args(["verticals", "enable", "quant"]).verticals_cmd == "enable"
    assert parser.parse_args(["verticals", "disable", "quant"]).verticals_cmd == "disable"
    assert parser.parse_args(["verticals", "refresh"]).verticals_cmd == "refresh"
    with pytest.raises(SystemExit, match="2"):
        parser.parse_args(["verticals"])
    assert "verticals" in tui_launcher._PYTHON_ADMIN_COMMANDS


def test_list_install_info_remove_round_trip(capsys) -> None:
    assert main(["verticals", "refresh"]) == 0
    assert "2 verticals (release vtest)" in capsys.readouterr().out

    assert main(["verticals", "list"]) == 0
    listing = capsys.readouterr().out
    assert "child_v" in listing and "available" in listing and "research" in listing and "builtin" in listing

    assert main(["verticals", "install", "child_v"]) == 0
    transcript = capsys.readouterr().out
    assert "child_v: install started" in transcript and "child_v: install finished" in transcript
    assert "[100%] child_v: install finished" in transcript
    # A local mirror installs faster than the poller ticks; the per-step trail is in the log.
    assert "base_v: downloading" in store.log_path(None, "child_v").read_text(encoding="utf-8")
    assert set(store.installed()) == {"base_v", "child_v"}

    assert main(["verticals", "info", "child_v"]) == 0
    info = capsys.readouterr().out
    assert "kind                 installed" in info and "requires             base_v" in info
    assert "purpose_zh           子垂直" in info

    assert main(["verticals", "info", "child_v", "--json"]) == 0
    row = json.loads(capsys.readouterr().out)
    assert row["kind"] == "installed" and row["actions"] == ["disable", "uninstall"]

    assert main(["verticals", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"verticals", "catalog", "host"}

    assert main(["verticals", "update"]) == 0
    assert "every installed vertical is current" in capsys.readouterr().out

    assert main(["verticals", "disable", "child_v"]) == 0
    assert "child_v: disabled" in capsys.readouterr().out
    assert store.installed()["child_v"]["enabled"] is False
    assert main(["verticals", "enable", "child_v"]) == 0

    assert main(["verticals", "remove", "base_v"]) == 1
    assert "required by installed vertical(s) child_v" in capsys.readouterr().err
    assert main(["verticals", "remove", "child_v"]) == 0
    assert "child_v: uninstall finished" in capsys.readouterr().out
    assert set(store.installed()) == {"base_v"}


def test_remove_of_a_used_vertical_needs_force_and_failures_exit_1(capsys) -> None:
    assert main(["verticals", "install", "base_v"]) == 0
    session = Path(os.environ["ARGUS_SKILL_HOME"]) / "projects" / "s-base-1"
    session.mkdir(parents=True)
    write_pipeline_state(session, {"vertical": "base_v"})
    capsys.readouterr()
    assert main(["verticals", "remove", "base_v"]) == 1
    assert "session(s) s-base-1" in capsys.readouterr().err
    assert main(["verticals", "remove", "base_v", "--force"]) == 0
    assert store.installed() == {}
    assert main(["verticals", "install", "ghost_v"]) == 1
    assert "not in the catalog" in capsys.readouterr().err
    assert main(["verticals", "info", "ghost_v"]) == 1
    assert main(["verticals", "install", "research"]) == 1
    assert "built-in" in capsys.readouterr().err


def test_refresh_reports_a_broken_catalog_with_exit_1(release, capsys) -> None:
    assert main(["verticals", "refresh"]) == 0
    capsys.readouterr()
    release.write_text("{", encoding="utf-8")
    assert main(["verticals", "refresh"]) == 1
    assert "not valid JSON" in capsys.readouterr().err


def test_unresolved_store_root_placeholder_exits_2(monkeypatch, capsys) -> None:
    monkeypatch.setenv(store.HOST_ROOT_ENV, "$ARGUS_NO_SUCH_PLACEHOLDER/verticals")
    monkeypatch.delenv("ARGUS_NO_SUCH_PLACEHOLDER", raising=False)
    assert main(["verticals", "list"]) == 2
    assert "unresolved placeholder" in capsys.readouterr().err


def test_python_dash_m_argus_runs_the_store_headless(release) -> None:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    result = subprocess.run(
        [sys.executable, "-m", "argus", "verticals", "list", "--json"],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [row["name"] for row in payload["verticals"] if row["kind"] == "available"] == ["base_v", "child_v"]
    assert "node" not in result.stderr.lower()
