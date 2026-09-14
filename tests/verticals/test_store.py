"""The Vertical Store: catalog, install/update/remove, guards, and the merged rows.

Every test runs against a synthetic release written into ``tmp_path`` (see
``fake_release``); one integration test builds the real community release
from a copy of the ``argus-verticals`` checkout when it is present.
"""
from __future__ import annotations

import json
import os
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest

from argus.core.pipeline_state import write_pipeline_state
from argus.skills import vertical_select
from argus.skills.builtins import iter_vertical_skill_texts
from argus.verticals import _registry, store
from argus.verticals._base import load_vertical
from tests.verticals import fake_release as fake


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    monkeypatch.delenv(store.HOST_ROOT_ENV, raising=False)
    monkeypatch.delenv("ARGUS_TRIAL_HARNESS", raising=False)
    monkeypatch.delenv(store.PREINSTALL_ENV, raising=False)
    _registry.refresh_vertical_plugins()
    yield
    _registry.refresh_vertical_plugins()
    store._purge_modules(["argus_verticals"])


@pytest.fixture
def home() -> Path:
    return Path(os.environ["ARGUS_SKILL_HOME"])


@pytest.fixture
def release(tmp_path, monkeypatch) -> Path:
    """base <- child (requires + skill parent); both literary-style share one helper tree."""
    catalog = fake.build_release(tmp_path / "dist", [
        fake.spec("base_v", shared=("argus_verticals/lit/shared",), python_requirements=("PyYAML>=6",)),
        fake.spec(
            "child_v", requires=("base_v",), parents=("base_v",), shared=("argus_verticals/lit/shared",),
            python_requirements=("no_such_distribution_xyz>=1",), purpose_zh="子垂直",
        ),
        fake.spec("solo_v", skills=False),
    ])
    monkeypatch.setenv(store.CATALOG_ENV, str(catalog))
    return catalog


def _rewrite_catalog(catalog: Path, mutate) -> None:
    data = json.loads(catalog.read_text(encoding="utf-8"))
    mutate(data)
    catalog.write_text(json.dumps(data), encoding="utf-8")


# --- catalog ---------------------------------------------------------------------


def test_catalog_source_defaults_to_the_latest_github_release(monkeypatch) -> None:
    monkeypatch.delenv(store.CATALOG_ENV, raising=False)
    assert store.catalog_source() == store.DEFAULT_CATALOG_URL
    assert store.catalog_source().startswith("https://github.com/Argus-AiTeam/argus-verticals/")


def test_local_catalog_is_loaded_cached_and_refreshed(release, home, monkeypatch) -> None:
    loaded = store.load_catalog()
    assert loaded["error"] == "" and loaded["source"] == str(release)
    assert set(loaded["catalog"]["verticals"]) == {"base_v", "child_v", "solo_v"}
    assert loaded["catalog"]["release"]["tag"] == "vtest"
    cache = json.loads(store.catalog_cache_path().read_text(encoding="utf-8"))
    assert cache["source"] == str(release) and cache["catalog"]["schema"] == 1

    fetches: list[str] = []
    original = store._fetch_catalog

    def counting(source):
        fetches.append(source)
        return original(source)

    monkeypatch.setattr(store, "_fetch_catalog", counting)
    store.load_catalog()
    assert fetches == []  # fresh cache, same source: no re-read
    store.load_catalog(max_age=timedelta(seconds=0))
    store.load_catalog(refresh=True)
    assert fetches == [str(release), str(release)]


def test_file_url_catalog_source_is_accepted(release, monkeypatch) -> None:
    monkeypatch.setenv(store.CATALOG_ENV, release.resolve().as_uri())
    assert "child_v" in store.load_catalog()["catalog"]["verticals"]


def test_stale_cache_is_served_with_the_error_when_the_fetch_fails(release, monkeypatch) -> None:
    store.load_catalog()
    release.write_text("{not json", encoding="utf-8")
    loaded = store.load_catalog(refresh=True)
    assert "child_v" in loaded["catalog"]["verticals"]
    assert "not valid JSON" in loaded["error"]
    store.catalog_cache_path().unlink()
    with pytest.raises(store.VerticalStoreError, match="not valid JSON"):
        store.load_catalog(refresh=True)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c.update(schema=2), "schema 2"),
        (lambda c: c["verticals"]["solo_v"]["archive"].update(url="https://evil.example/solo.zip"), "not allowed"),
        (lambda c: c["verticals"]["solo_v"]["archive"].update(url="http://github.com/x.zip"), "not https"),
        (lambda c: c["verticals"]["solo_v"]["archive"].update(sha256="nope"), "sha256"),
        (lambda c: c["verticals"]["solo_v"].update(module="argus_verticals.other.stages"), "does not live in paths"),
        (lambda c: c["verticals"]["solo_v"].update(paths=["../escape"]), "invalid entry"),
        (lambda c: c["verticals"]["solo_v"].update(requires=["ghost_v"]), "unknown vertical 'ghost_v'"),
        (lambda c: c["verticals"]["solo_v"].update(requires=["solo_v"]), "requires itself"),
        (lambda c: c["verticals"]["solo_v"].pop("archive"), "archive is missing"),
    ],
    ids=["schema", "foreign-host", "plain-http", "bad-sha", "module-outside-paths", "path-escape",
         "unknown-requires", "self-requires", "index-not-release"],
)
def test_invalid_catalogs_are_rejected_by_name(release, mutate, message) -> None:
    data = json.loads(release.read_text(encoding="utf-8"))
    mutate(data)
    with pytest.raises(store.VerticalStoreError, match=message):
        store.validate_catalog(data, local_source=False)


def test_local_archive_urls_need_a_local_catalog(release) -> None:
    data = json.loads(release.read_text(encoding="utf-8"))
    data["verticals"]["solo_v"]["archive"]["url"] = "file:///tmp/solo.zip"
    assert store.validate_catalog(data, local_source=True)["verticals"]["solo_v"]["archive"]["url"].startswith("file://")
    with pytest.raises(store.VerticalStoreError, match="local archives need a local catalog"):
        store.validate_catalog(data, local_source=False)


def test_https_downloads_never_leave_the_allow_list(monkeypatch) -> None:
    for url in ("http://github.com/a.zip", "https://evil.example/a.zip", "https://user@github.com/a.zip"):
        with pytest.raises(store.VerticalStoreError):
            store._https_stream(url, lambda _: None, limit=10, what="archive")

    class Response:
        is_redirect = True
        headers = {"location": "https://evil.example/elsewhere.zip"}

        def __init__(self, url):
            import httpx

            self.url = httpx.URL(url)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def stream(self, method, url, follow_redirects=False):
            assert follow_redirects is False
            return Response(url)

    monkeypatch.setattr(store.httpx, "Client", Client)
    with pytest.raises(store.VerticalStoreError, match="redirect host 'evil.example' is not allowed"):
        store._https_stream(
            "https://github.com/Argus-AiTeam/argus-verticals/releases/download/v0/a.zip",
            lambda _: None, limit=10, what="archive",
        )


# --- install ---------------------------------------------------------------------


def test_install_resolves_the_requires_closure_dependencies_first(release, home) -> None:
    final = store.install("child_v", wait=True)
    assert final["status"] == "done" and final["progress"] == 100 and final["action"] == "install"
    assert {"status", "action", "progress", "message", "started", "finished", "pid"} <= set(final)
    present = store.installed()
    assert set(present) == {"base_v", "child_v"}
    assert present["base_v"]["installed_at"] <= present["child_v"]["installed_at"]
    assert present["child_v"]["requires"] == ["base_v"]
    assert present["child_v"]["source"] == {
        "repo": "Argus-AiTeam/argus-verticals", "tag": "vtest",
        "url": "https://github.com/Argus-AiTeam/argus-verticals/releases/download/vtest/child_v-0.1.0.zip",
    }
    package = store.package_root()
    assert package == home / "verticals" / "argus_verticals"
    assert (package / "child_v" / "stages.py").is_file()
    assert (package / "lit" / "shared" / "__init__.py").is_file()
    assert not (package / "__init__.py").exists()
    assert not list((home / "verticals" / ".staging").iterdir()) if (home / "verticals" / ".staging").exists() else True
    log = store.log_path(None, "child_v").read_text(encoding="utf-8")
    assert "base_v: downloading" in log and "child_v: installed 0.1.0" in log


def test_installed_vertical_is_selectable_loadable_and_seeds_parent_skills_first(release) -> None:
    store.install("child_v", wait=True)
    assert "child_v" in vertical_select.available_verticals()
    assert vertical_select.available_vertical_purposes()["child_v"] == "synthetic vertical child_v"
    module = load_vertical("child_v")
    assert Path(module.__file__).resolve().is_relative_to(store.package_root().resolve())
    assert _registry.vertical_plugin("child_v").origin == "store"
    assert [name for name, _ in iter_vertical_skill_texts("child_v")] == ["engineer/base_v.md", "engineer/child_v.md"]
    assert vertical_select.require_vertical("child_v") == "child_v"


def test_shared_tree_ownership_survives_the_first_owner_leaving(release, home) -> None:
    store.install("child_v", wait=True)
    shared = store.registry()["shared"]["argus_verticals/lit/shared"]
    assert shared["owners"] == ["base_v", "child_v"]
    assert set(shared["sha256s"]) == {"base_v", "child_v"}
    store.uninstall("child_v", wait=True)
    assert store.registry()["shared"]["argus_verticals/lit/shared"]["owners"] == ["base_v"]
    assert (store.package_root() / "lit" / "shared").is_dir()
    assert not (store.package_root() / "child_v").exists()
    store.uninstall("base_v", wait=True)
    assert store.registry() == {"schema": 1, "verticals": {}, "shared": {}}
    assert not (store.package_root() / "lit").exists()
    assert "argus_verticals.base_v.stages" not in __import__("sys").modules


def test_installing_a_current_vertical_or_a_builtin_or_an_unknown_name_is_refused(release) -> None:
    store.install("solo_v", wait=True)
    with pytest.raises(store.VerticalStoreError, match="already installed"):
        store.install("solo_v")
    store.disable("solo_v")
    with pytest.raises(store.VerticalStoreError, match="enable it instead"):
        store.install("solo_v")
    with pytest.raises(store.VerticalStoreError, match="built-in"):
        store.install("research")
    with pytest.raises(store.UnknownVerticalError):
        store.install("ghost_v")
    with pytest.raises(store.UnknownVerticalError):
        store.install("Not A Name")


def test_a_second_job_on_the_same_vertical_is_refused_while_one_runs(release, monkeypatch) -> None:
    started = __import__("threading").Event()
    release_job = __import__("threading").Event()

    def slow(*args, progress):
        started.set()
        release_job.wait(5)

    monkeypatch.setattr(store, "_install_job", slow)
    op = store.install("solo_v")
    assert op["status"] == "running"
    started.wait(5)
    with pytest.raises(store.VerticalStoreError, match="already running"):
        store.install("solo_v")
    release_job.set()
    assert store.wait_for_operation("solo_v")["status"] == "done"


def test_a_job_whose_process_died_is_reported_failed(release) -> None:
    store.install("solo_v", wait=True)
    path = store.operation_path(None, "solo_v")
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(status="running", pid=2**22 - 7, identity={"start_ticks": 1})
    path.write_text(json.dumps(record), encoding="utf-8")
    op = store.operation("solo_v")
    assert op["status"] == "failed" and "ended before it finished" in op["message"]
    assert "identity" not in op


# --- update ------------------------------------------------------------------------


def test_update_reinstalls_when_the_catalog_version_changes(release, tmp_path) -> None:
    store.install("solo_v", wait=True)
    with pytest.raises(store.VerticalStoreError, match="already current"):
        store.update("solo_v")
    assert next(r for r in store.rows() if r["name"] == "solo_v")["update_available"] is False

    fake.build_release(tmp_path / "dist", [
        fake.spec("base_v", shared=("argus_verticals/lit/shared",)),
        fake.spec("child_v", requires=("base_v",), parents=("base_v",), shared=("argus_verticals/lit/shared",)),
        fake.spec("solo_v", version="0.2.0", marker=" v2", skills=False),
    ])
    store.load_catalog(refresh=True)
    row = next(r for r in store.rows() if r["name"] == "solo_v")
    assert row["update_available"] is True and row["version"] == "0.2.0" and row["installed_version"] == "0.1.0"
    assert "update" in row["actions"]

    final = store.update("solo_v", wait=True)
    assert final["status"] == "done"
    assert store.installed()["solo_v"]["version"] == "0.2.0"
    assert load_vertical("solo_v").MARKER == " v2"
    assert vertical_select.available_vertical_purposes()["solo_v"].endswith("v2")


def test_replacing_a_tree_keeps_a_nested_installed_vertical(release, tmp_path) -> None:
    """``digital_circuit/benchmark`` lives inside ``digital_circuit``; updating the parent keeps it."""
    dist = tmp_path / "dist"
    parent = fake.spec("parent_v")
    nested = fake.spec("parent_v_bench", requires=("parent_v",))
    catalog = fake.build_release(dist, [parent, nested])
    # Re-home the nested vertical inside the parent's directory, as the community layout does.
    members = {k.replace("argus_verticals/parent_v_bench/", "argus_verticals/parent_v/bench/"): v
               for k, v in fake.archive_members(nested).items()}
    entry = fake.write_raw_archive(dist, nested, members)
    entry.update(module="argus_verticals.parent_v.bench.stages", paths=["argus_verticals/parent_v/bench"])
    _rewrite_catalog(catalog, lambda c: c["verticals"].update(parent_v_bench=entry))
    store.load_catalog(refresh=True)
    store.install("parent_v_bench", wait=True)
    assert (store.package_root() / "parent_v" / "bench" / "stages.py").is_file()

    parent2 = fake.spec("parent_v", version="0.2.0", marker=" v2")
    data = fake.zip_bytes(fake.archive_members(parent2))
    (dist / "parent_v-0.2.0.zip").write_bytes(data)
    _rewrite_catalog(catalog, lambda c: c["verticals"].update(parent_v=fake.catalog_entry(parent2, data, tag="vtest")))
    store.load_catalog(refresh=True)
    store.update("parent_v", wait=True)
    assert store.installed()["parent_v"]["version"] == "0.2.0"
    assert (store.package_root() / "parent_v" / "bench" / "stages.py").is_file()
    assert "parent_v_bench" in vertical_select.available_verticals()


# --- enable / disable ------------------------------------------------------------


def test_enable_and_disable_flip_the_flag_and_the_advertised_set(release) -> None:
    store.install("solo_v", wait=True)
    assert store.disable("solo_v")["enabled"] is False
    assert store.registry()["verticals"]["solo_v"]["enabled"] is False
    assert "solo_v" not in vertical_select.available_verticals()
    assert store.enable("solo_v")["enabled"] is True
    assert "solo_v" in vertical_select.available_verticals()
    with pytest.raises(store.UnknownVerticalError):
        store.enable("child_v")


# --- uninstall guards --------------------------------------------------------------


def test_uninstall_refuses_a_vertical_a_local_session_still_uses_unless_forced(release, home) -> None:
    store.install("solo_v", wait=True)
    session = home / "projects" / "s-solo-1"
    session.mkdir(parents=True)
    write_pipeline_state(session, {"vertical": "solo_v", "current_stage": "work"})
    other = home / "projects" / "s-other"
    other.mkdir()
    write_pipeline_state(other, {"vertical": "research-needed"})
    assert store.used_by("solo_v") == ["s-solo-1"]
    assert next(r for r in store.rows() if r["name"] == "solo_v")["used_by"] == ["s-solo-1"]
    with pytest.raises(store.VerticalStoreError, match=r"session\(s\) s-solo-1"):
        store.uninstall("solo_v")
    assert "solo_v" in store.installed()
    assert store.uninstall("solo_v", force=True, wait=True)["status"] == "done"
    assert "solo_v" not in store.installed()


def test_uninstall_refuses_a_dependency_and_keeps_dependencies_of_the_removed(release) -> None:
    store.install("child_v", wait=True)
    with pytest.raises(store.VerticalStoreError, match="required by installed vertical\\(s\\) child_v"):
        store.uninstall("base_v")
    store.uninstall("child_v", wait=True)
    assert set(store.installed()) == {"base_v"}
    with pytest.raises(store.UnknownVerticalError, match="not installed"):
        store.uninstall("child_v")


# --- host-managed deployments -----------------------------------------------------


@pytest.mark.parametrize("env", ["ARGUS_TRIAL_HARNESS", store.HOST_ROOT_ENV])
def test_host_managed_roots_refuse_install_update_and_remove_but_allow_toggling(release, tmp_path, monkeypatch, env) -> None:
    store.install("solo_v", wait=True)
    value = str(store.store_root()) if env == store.HOST_ROOT_ENV else "argus-pi"
    monkeypatch.setenv(env, value)
    assert store.managed_by_host() is True
    if env == store.HOST_ROOT_ENV:
        assert store.store_root(tmp_path / "elsewhere") == store.store_root()
    for action in (store.install, store.update, store.uninstall):
        with pytest.raises(store.VerticalStoreError, match="provided by the host"):
            action("solo_v")
    assert store.disable("solo_v")["enabled"] is False
    assert store.enable("solo_v")["enabled"] is True
    row = next(r for r in store.rows() if r["name"] == "solo_v")
    assert row["managed_by_host"] is True and row["actions"] == ["disable"]
    available = next(r for r in store.rows() if r["name"] == "child_v")
    assert available["actions"] == []


def test_preinstall_prepares_declared_verticals_even_under_a_host_root(release, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(store.HOST_ROOT_ENV, str(tmp_path / "prepared"))
    monkeypatch.setenv(store.PREINSTALL_ENV, "child_v, solo_v,child_v")
    assert store.preinstalled_names() == ["child_v", "solo_v"]
    results = store.preinstall()
    assert {name: r["status"] for name, r in results.items()} == {"child_v": "done", "solo_v": "done"}
    assert (tmp_path / "prepared" / "argus_verticals" / "base_v" / "stages.py").is_file()
    store.disable("solo_v")
    again = store.preinstall()
    assert again == {"child_v": {"status": "ready"}, "solo_v": {"status": "done", "action": "enable"}}
    assert store.preinstall(names=["ghost_v"])["ghost_v"]["status"] == "failed"


# --- tampered or broken archives leave the store untouched ---------------------------


def _catalog_with(release: Path, entry: dict) -> None:
    _rewrite_catalog(release, lambda c: c["verticals"].update({entry["name"]: entry}))
    store.load_catalog(refresh=True)


def test_sha256_mismatch_installs_nothing(release, home) -> None:
    item = fake.spec("solo_v")
    _catalog_with(release, fake.write_raw_archive(release.parent, item, fake.archive_members(item), wrong_sha=True))
    with pytest.raises(store.VerticalStoreError, match="sha256 .* does not match"):
        store.install("solo_v", wait=True)
    assert store.installed() == {} and not (store.package_root() / "solo_v").exists()
    assert store.operation("solo_v")["status"] == "failed"


def test_a_corrupt_archive_fails_atomically_and_an_update_keeps_the_old_version(release, home) -> None:
    store.install("solo_v", wait=True)
    before = (store.package_root() / "solo_v" / "stages.py").read_text(encoding="utf-8")
    item = fake.spec("solo_v", version="0.2.0", marker=" v2")
    _catalog_with(release, fake.write_raw_archive(release.parent, item, fake.archive_members(item), corrupt=True))
    with pytest.raises(store.VerticalStoreError, match="not a valid zip|corrupt"):
        store.update("solo_v", wait=True)
    assert store.installed()["solo_v"]["version"] == "0.1.0"
    assert (store.package_root() / "solo_v" / "stages.py").read_text(encoding="utf-8") == before
    assert not (home / "verticals" / ".staging").exists() or not list((home / "verticals" / ".staging").iterdir())


@pytest.mark.parametrize(
    ("members", "symlink", "message"),
    [
        ({"argus_verticals/solo_v/stages.py": "x", "../escape.py": "evil"}, None, "outside the vertical's trees"),
        ({"argus_verticals/solo_v/stages.py": "x", "argus_verticals/solo_v/../../evil.py": "evil"}, None, "outside"),
        ({"argus_verticals/solo_v/stages.py": "x", "/abs/evil.py": "evil"}, None, "outside"),
        ({"argus_verticals/solo_v/stages.py": "x", "argus_verticals/__init__.py": ""}, None, "outside"),
        ({"argus_verticals/solo_v/stages.py": "x", "argus_verticals/other_v/stages.py": "x"}, None, "outside"),
        ({"argus_verticals/solo_v/stages.py": "x"}, "argus_verticals/solo_v/link", "symlink"),
        ({"argus_verticals/solo_v/__init__.py": ""}, None, "no argus_verticals/solo_v/stages.py"),
    ],
    ids=["dotdot", "inner-dotdot", "absolute", "package-root-init", "foreign-vertical", "symlink", "no-stages"],
)
def test_unsafe_archives_are_refused_before_anything_is_written(release, home, members, symlink, message) -> None:
    item = fake.spec("solo_v")
    _catalog_with(release, fake.write_raw_archive(release.parent, item, members, symlink=symlink))
    with pytest.raises(store.VerticalStoreError, match=message):
        store.install("solo_v", wait=True)
    assert store.installed() == {}
    assert not (store.package_root()).exists() or not list(store.package_root().iterdir())


def test_oversized_archives_are_refused(release, monkeypatch) -> None:
    monkeypatch.setattr(store, "ARCHIVE_LIMIT", 10)
    with pytest.raises(store.VerticalStoreError, match="size"):
        store.install("solo_v", wait=True)


# --- rows and the API payload --------------------------------------------------------


def test_rows_merge_builtins_installed_and_available_with_actions(release) -> None:
    store.install("child_v", wait=True)
    rows = {row["name"]: row for row in store.rows()}
    assert list(rows)[: len(vertical_select.VERTICALS)] == list(vertical_select.VERTICALS)
    research = rows["research"]
    assert research["kind"] == "builtin" and research["purpose_zh"] is None and research["actions"] == []
    assert research["purpose"] == vertical_select.VERTICAL_PURPOSES["research"] and research["enabled"] is True
    child = rows["child_v"]
    assert child["kind"] == "installed" and child["purpose_zh"] == "子垂直"
    assert child["version"] == child["installed_version"] == "0.1.0"
    assert child["requires"] == ["base_v"] and child["shared"] == ["argus_verticals/lit/shared"]
    assert child["python_requirements"] == ["no_such_distribution_xyz>=1"]
    assert child["missing_python"] == ["no_such_distribution_xyz"]
    assert rows["base_v"]["missing_python"] == []  # PyYAML -> yaml is importable here
    assert child["actions"] == ["disable", "uninstall"] and child["operation"]["status"] == "done"
    solo = rows["solo_v"]
    assert solo["kind"] == "available" and solo["installed_version"] is None and solo["enabled"] is False
    assert solo["actions"] == ["install"] and solo["operation"] is None and solo["tags"] == ["synthetic", "test"]
    expected_keys = {
        "name", "purpose", "purpose_zh", "kind", "version", "installed_version", "enabled",
        "update_available", "requires", "shared", "python_requirements", "missing_python", "tags",
        "size_bytes", "used_by", "operation", "managed_by_host", "actions",
    }
    assert all(set(row) == expected_keys for row in rows.values())
    assert {row["kind"] for row in rows.values()} <= set(store.KINDS)


def test_overview_reports_catalog_and_host_status(release, home) -> None:
    payload = store.overview()
    assert set(payload) == {"verticals", "catalog", "host"}
    assert payload["catalog"]["source"] == str(release) and payload["catalog"]["release_tag"] == "vtest"
    assert payload["catalog"]["error"] == "" and payload["catalog"]["fetched_at"] > 0
    assert payload["host"] == {"managed_by_host": False, "store_root": str(home / "verticals")}
    release.write_text("{", encoding="utf-8")
    store.catalog_cache_path().unlink()
    broken = store.overview(refresh=True)
    assert broken["catalog"]["release_tag"] is None and "not valid JSON" in broken["catalog"]["error"]
    assert [row["kind"] for row in broken["verticals"]] == ["builtin"] * len(vertical_select.VERTICALS)


def test_requirement_names_map_to_importable_modules() -> None:
    assert store.requirement_module("PyYAML>=6") == "yaml"
    assert store.requirement_module("scikit-learn>=1.4") == "sklearn"
    assert store.requirement_module("mplfinance>=0.12.10b0") == "mplfinance"
    assert store.requirement_module("torch>=2.2; sys_platform != 'win32'") == "torch"
    assert store.missing_python(["json>=1", "definitely_missing_module_abc>=1"]) == ["definitely_missing_module_abc"]


def test_an_unreadable_registry_is_an_error_not_an_empty_store(release, home) -> None:
    store.install("solo_v", wait=True)
    store.registry_path().write_text("{broken", encoding="utf-8")
    with pytest.raises(store.VerticalStoreError, match="not valid JSON"):
        store.registry()
    assert next(r for r in store.rows() if r["name"] == "solo_v")["kind"] == "available"
    store.registry_path().write_text(json.dumps({"schema": 9, "verticals": {}}), encoding="utf-8")
    with pytest.raises(store.VerticalStoreError, match="schema 9"):
        store.installed()


def test_requires_cycles_in_a_catalog_are_reported(release) -> None:
    catalog = store.load_catalog()["catalog"]
    catalog["verticals"]["base_v"]["requires"] = ["child_v"]
    with pytest.raises(store.VerticalStoreError, match="requires cycle"):
        store._closure(catalog, ["child_v"])


# --- the real community release ---------------------------------------------------------


@pytest.fixture(scope="session")
def community_release(tmp_path_factory) -> Path:
    catalog = fake.build_community_release(tmp_path_factory.mktemp("community"))
    if catalog is None:
        pytest.skip(f"argus-verticals checkout not found (set {fake.COMMUNITY_REPO_ENV})")
    return catalog


@pytest.mark.integration
def test_real_community_archives_install_load_and_seed_skills(community_release, monkeypatch, home) -> None:
    monkeypatch.setenv(store.CATALOG_ENV, str(community_release))
    loaded = store.load_catalog(refresh=True)
    assert len(loaded["catalog"]["verticals"]) == 17
    store.install("chip_design", wait=True)
    assert set(store.installed()) == {"digital_circuit", "chip_design"}
    assert {"chip_design", "digital_circuit"} <= set(vertical_select.available_verticals())
    module = load_vertical("chip_design")
    assert Path(module.__file__).resolve().is_relative_to(store.package_root().resolve())
    names = [name for name, _ in iter_vertical_skill_texts("chip_design")]
    digital = [name for name, _ in iter_vertical_skill_texts("digital_circuit")]
    assert digital and names[: len(digital)] == digital and len(names) > len(digital)
    with pytest.raises(store.VerticalStoreError, match="required by installed vertical"):
        store.uninstall("digital_circuit")

    store.install("prose", wait=True)
    store.install("modern_poetry", wait=True)
    shared = store.registry()["shared"]["argus_verticals/literary/shared"]
    assert shared["owners"] == ["prose", "modern_poetry"]
    assert len(set(shared["sha256s"].values())) == 1  # both archives ship the same helper tree
    store.uninstall("prose", wait=True)
    assert (store.package_root() / "literary" / "shared").is_dir()
    assert load_vertical("modern_poetry").VERTICAL_PURPOSE

    store.uninstall("chip_design", wait=True)
    assert "digital_circuit" in store.installed() and "chip_design" not in store.installed()
    zips = sorted(p.name for p in community_release.parent.glob("*.zip"))
    assert len(zips) == 17
    with zipfile.ZipFile(community_release.parent / "chip_design-0.1.0.zip") as zf:
        assert all(name.startswith("argus_verticals/chip_design/") for name in zf.namelist())
