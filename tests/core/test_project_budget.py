from __future__ import annotations

import json
import threading
import time

import pytest

from argus_skill.core.knobs import resolve_budget_caps
from argus_skill.core.project_budget import (
    GlobalBudget,
    ProjectBudget,
    budget_path,
    global_budget_path,
    read_global_budget,
    read_project_budget,
    update_project_budget,
    write_global_budget,
    write_project_budget,
)


@pytest.fixture(autouse=True)
def _isolate_budget_config(tmp_path_factory, monkeypatch):
    """config.json (knob store) is now the single source of truth for budget
    caps. Isolate each test's config.json (via ARGUS_SKILL_HOME) and clear the
    cap env vars so pollution from another test's os.environ writes cannot leak in.
    """
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path_factory.mktemp("budget-home")))
    for name in (
        "ARGUS_SKILL_PER_MISSION_CAP_USD",
        "ARGUS_SKILL_DAILY_CAP_USD",
        "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD",
    ):
        monkeypatch.delenv(name, raising=False)


def test_missing_budget_migrates_env_once(tmp_path) -> None:
    first = read_project_budget(
        tmp_path,
        migrate_env={
            "ARGUS_SKILL_PER_MISSION_CAP_USD": "12.5",
            "ARGUS_SKILL_DAILY_CAP_USD": "75",
            "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "120",
        },
    )
    assert first == ProjectBudget(12.5, 75.0)

    second = read_project_budget(
        tmp_path,
        migrate_env={
            "ARGUS_SKILL_PER_MISSION_CAP_USD": "30",
            "ARGUS_SKILL_DAILY_CAP_USD": "180",
            "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "30",
        },
    )
    assert second == first


def test_update_changes_only_requested_field(tmp_path) -> None:
    write_project_budget(tmp_path, ProjectBudget(10, 20))
    updated = update_project_budget(tmp_path, daily_cap_usd="25")
    assert updated == ProjectBudget(10, 25)


def test_malformed_budget_is_not_overwritten_by_defaults(tmp_path) -> None:
    path = budget_path(tmp_path)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid project budget file"):
        read_project_budget(tmp_path)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_budget_file_has_explicit_schema_and_numeric_values(tmp_path) -> None:
    write_project_budget(tmp_path, ProjectBudget(1, 2))
    payload = json.loads(budget_path(tmp_path).read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": 1,
        "per_mission_cap_usd": 1,
        "daily_cap_usd": 2,
    }


def test_env_and_config_beat_legacy_budget_file(tmp_path) -> None:
    # Option A: config.json/env is the single source; a pre-existing budget.json
    # no longer overrides an explicit cap on restart.
    global_root = tmp_path / "home"
    write_global_budget(global_root, GlobalBudget(99))
    write_project_budget(tmp_path, ProjectBudget(9, 49))
    caps = resolve_budget_caps(
        project_state_dir=tmp_path,
        global_root=global_root,
        env={
            "ARGUS_SKILL_PER_MISSION_CAP_USD": "30",
            "ARGUS_SKILL_DAILY_CAP_USD": "180",
            "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "30",
        },
    )
    assert caps.per_mission_cap_usd == 30
    assert caps.daily_cap_usd == 180
    assert caps.global_daily_cap_usd == 30


def test_env_beats_legacy_global_budget_file(tmp_path) -> None:
    write_global_budget(tmp_path, GlobalBudget(7))
    caps = resolve_budget_caps(
        global_root=tmp_path,
        env={"ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "999"},
        persisted={},
    )
    assert caps.global_daily_cap_usd == 999


def test_config_json_cap_is_read_live_by_resolve(tmp_path, monkeypatch) -> None:
    # config.json (knob store) is the single source — resolve reads it live.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    (home / "config.json").write_text(
        json.dumps(
            {
                "ARGUS_SKILL_PER_MISSION_CAP_USD": "55",
                "ARGUS_SKILL_DAILY_CAP_USD": "777",
                "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "8888",
            }
        ),
        encoding="utf-8",
    )
    proj = home / "projects" / "p"
    proj.mkdir(parents=True)
    caps = resolve_budget_caps(project_state_dir=proj, global_root=home)
    assert caps.per_mission_cap_usd == 55
    assert caps.daily_cap_usd == 777
    assert caps.global_daily_cap_usd == 8888


def test_legacy_budget_files_migrate_into_config_once(tmp_path, monkeypatch) -> None:
    # An upgrading install with a pre-existing budget.json keeps its caps: they
    # migrate into config.json once, then config.json is authoritative.
    from argus_skill.core.knob_store import read_persisted_knobs

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    proj = home / "projects" / "p"
    proj.mkdir(parents=True)
    write_project_budget(proj, ProjectBudget(9, 49))
    write_global_budget(home, GlobalBudget(99))
    caps = resolve_budget_caps(project_state_dir=proj, global_root=home)
    assert caps.per_mission_cap_usd == 9
    assert caps.daily_cap_usd == 49
    assert caps.global_daily_cap_usd == 99
    persisted = read_persisted_knobs()
    assert persisted.get("ARGUS_SKILL_DAILY_CAP_USD") in ("49", "49.0")
    assert persisted.get("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD") in ("99", "99.0")


def test_global_budget_default_is_20000(tmp_path) -> None:
    assert read_global_budget(tmp_path) == GlobalBudget(20_000.0)


def test_project_budget_defaults_support_long_running_missions(tmp_path) -> None:
    assert read_project_budget(tmp_path, migrate_env={}) == ProjectBudget(
        300.0,
        2_000.0,
    )


def test_global_budget_migrates_once_and_ignores_later_env(tmp_path) -> None:
    first = read_global_budget(
        tmp_path,
        migrate_env={"ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "55"},
    )
    second = read_global_budget(
        tmp_path,
        migrate_env={"ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "999"},
    )
    assert first == second == GlobalBudget(55)
    assert global_budget_path(tmp_path).exists()


def test_concurrent_project_budget_updates_do_not_lose_fields(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.core import project_budget

    write_project_budget(tmp_path, ProjectBudget(1, 2))
    original = project_budget._load_project
    start = threading.Barrier(2)

    def slow_load(path):
        value = original(path)
        time.sleep(0.05)
        return value

    monkeypatch.setattr(project_budget, "_load_project", slow_load)

    def update(**changes):
        start.wait()
        update_project_budget(tmp_path, **changes)

    first = threading.Thread(target=update, kwargs={"per_mission_cap_usd": 10})
    second = threading.Thread(target=update, kwargs={"daily_cap_usd": 20})
    first.start()
    second.start()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert read_project_budget(tmp_path) == ProjectBudget(10, 20)
