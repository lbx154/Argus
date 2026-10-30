"""``argus verticals``: headless, synchronous, exit codes 0/1/2, routed away from Node."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from argus.apps.cli import main
from argus.core.pipeline_state import write_pipeline_state
from argus.verticals import _registry, store
from tests.verticals import fake_release as fake


@pytest.fixture(autouse=True)
def release(tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv(store.HOST_ROOT_ENV, raising=False)
    catalog = fake.build_release(tmp_path / "dist", [
        fake.spec("base_v"),
        fake.spec("child_v", requires=("base_v",), parents=("base_v",), purpose_zh="子垂直"),
    ])
    monkeypatch.setenv(store.CATALOG_ENV, str(catalog))
    _registry.refresh_vertical_plugins()
    yield catalog
    _registry.refresh_vertical_plugins()
    store._purge_modules(["argus_verticals"])


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
    assert "child_v" in store.disabled_names() and "enabled" not in store.installed()["child_v"]
    assert main(["verticals", "enable", "child_v"]) == 0

    assert main(["verticals", "remove", "base_v"]) == 1
    assert "required by installed vertical(s) child_v" in capsys.readouterr().err
    assert main(["verticals", "uninstall", "child_v"]) == 0
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


def test_runtime_refusal_reaches_cli_info_list_and_install(release, capsys):
    payload = json.loads(release.read_text(encoding="utf-8"))
    payload["verticals"]["base_v"]["argus_features"] = ["future-runtime"]
    release.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["verticals", "info", "child_v"]) == 0
    assert "base_v: unsupported Argus feature 'future-runtime'" in capsys.readouterr().out
    assert main(["verticals", "list"]) == 0
    assert "future-runtime" in capsys.readouterr().err
    assert main(["verticals", "install", "child_v"]) == 1
    assert "base_v: unsupported Argus feature 'future-runtime'" in capsys.readouterr().err
    assert store.installed() == {} and store.operation("child_v") is None
