"""Release artifacts must exist and the Python manifest must track source."""

import json
from pathlib import Path

import pytest

from argus.release import source_files
from argus.release_tools import check_artifacts


@pytest.fixture
def release_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = tmp_path / "argus" / "app.py"
    source.parent.mkdir()
    source.write_text("VERSION = 1\n", encoding="utf-8")
    web = tmp_path / "frontend" / "web" / "dist"
    (web / "assets").mkdir(parents=True)
    manifest = source.parent / "release_manifest.json"
    manifest.write_text(json.dumps({"source_files": source_files(tmp_path)}))
    (web / "index.html").write_text('<script type="module" src="./assets/entry.js"></script>')
    (web / "assets" / "entry.js").write_text('// web entry')
    monkeypatch.setattr(check_artifacts, "ROOT", tmp_path)
    monkeypatch.setattr(check_artifacts, "MANIFEST", manifest)
    monkeypatch.setattr(check_artifacts, "WEB_INDEX", web / "index.html")
    return tmp_path


def test_current_source_and_frontends_pass(release_tree: Path) -> None:
    assert check_artifacts.check() == []


def test_source_update_rejects_a_stale_manifest(
    release_tree: Path,
) -> None:
    (release_tree / "argus" / "app.py").write_text("VERSION = 20\n")

    failures = check_artifacts.check()
    assert len(failures) == 1
    assert "does not match current source" in failures[0]


def test_current_entry_must_exist_even_when_an_old_asset_is_present(
    release_tree: Path,
) -> None:
    web = release_tree / "frontend" / "web" / "dist"
    (web / "assets" / "entry.js").rename(web / "assets" / "historical.js")
    with pytest.raises(RuntimeError, match="missing release artifact"):
        check_artifacts.check()


def test_checked_in_frontends_match_the_checked_in_source():
    """The existing Python CI must reject a stale shipped release."""
    assert check_artifacts.check() == []
