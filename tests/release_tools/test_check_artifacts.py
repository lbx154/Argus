"""A matching old release id must not hide newer source in a checkout."""

import json
from pathlib import Path

import pytest

from argus.release import compute_source_digest
from argus.release_tools import check_artifacts


@pytest.fixture
def release_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = tmp_path / "argus" / "app.py"
    source.parent.mkdir()
    source.write_text("VERSION = 1\n", encoding="utf-8")
    web = tmp_path / "frontend" / "web" / "dist"
    (web / "assets").mkdir(parents=True)
    tui = tmp_path / "frontend" / "tui" / "bundle" / "argus.mjs"
    tui.parent.mkdir(parents=True)
    digest = compute_source_digest(tmp_path)
    release_id = f"0.1.8+{digest[:16]}"
    manifest = source.parent / "release_manifest.json"
    manifest.write_text(json.dumps({"release_id": release_id, "source_digest": digest}))
    (web / "index.html").write_text('<script type="module" src="./assets/entry.js"></script>')
    (web / "assets" / "entry.js").write_text(f'export const release = "{release_id}";')
    tui.write_text(f'export const release = "{release_id}";')
    monkeypatch.setattr(check_artifacts, "ROOT", tmp_path)
    monkeypatch.setattr(check_artifacts, "MANIFEST", manifest)
    monkeypatch.setattr(check_artifacts, "TUI_BUNDLE", tui)
    monkeypatch.setattr(check_artifacts, "WEB_INDEX", web / "index.html")
    return tmp_path


def test_current_source_and_frontends_pass(release_tree: Path) -> None:
    assert check_artifacts.check() == []


def test_source_update_rejects_old_frontends_even_when_their_release_ids_match(
    release_tree: Path,
) -> None:
    (release_tree / "argus" / "app.py").write_text("VERSION = 2\n")

    failures = check_artifacts.check()
    assert len(failures) == 1
    assert "does not match current source" in failures[0]


def test_current_entry_must_match_even_when_an_old_asset_has_the_release_id(
    release_tree: Path,
) -> None:
    web = release_tree / "frontend" / "web" / "dist"
    (web / "assets" / "entry.js").rename(web / "assets" / "historical.js")
    (web / "assets" / "entry.js").write_text('export const release = "outdated";')

    failures = check_artifacts.check()
    assert len(failures) == 1
    assert "entry bundle does not embed current release" in failures[0]


def test_checked_in_frontends_match_the_checked_in_source():
    """The existing Python CI must reject a stale shipped release."""
    assert check_artifacts.check() == []
