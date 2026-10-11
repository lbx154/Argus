"""The launcher binds local frontend reuse to its selected data directory."""

from pathlib import Path

from argus.apps import tui_launcher


def test_exports_state_root_without_creating_it(tmp_path: Path, monkeypatch) -> None:
    selected = tmp_path / "new-state"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(selected))
    monkeypatch.setattr(tui_launcher, "_tui_local_identity", lambda: {})
    monkeypatch.setenv("ARGUS_TUI_LOCAL_STATE_ROOT", "stale")
    tui_launcher._export_tui_local_identity()
    assert tui_launcher.os.environ["ARGUS_TUI_LOCAL_STATE_ROOT"] == str(selected.resolve())
    assert not selected.exists()


def test_explicit_life_directory_precedes_export(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "previous"))
    monkeypatch.setenv("ARGUS_TUI_LOCAL_STATE_ROOT", "stale")
    monkeypatch.setattr(tui_launcher, "_tui_local_identity", lambda: {})
    selected = tmp_path / "chosen"
    forwarded = tui_launcher._configure_tui_life_dir(["--life-dir", str(selected), "--web"])
    tui_launcher._export_tui_local_identity()
    assert forwarded == ["--web"]
    assert tui_launcher.os.environ["ARGUS_TUI_LOCAL_STATE_ROOT"] == str(selected.resolve())
