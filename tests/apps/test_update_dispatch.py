from pathlib import Path
from types import SimpleNamespace

from argus_skill.apps import package_update, update


def test_update_dispatches_installed_package_without_source_git(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(update, "source_root", lambda: tmp_path)
    monkeypatch.setattr(package_update, "update_installed_package", lambda: SimpleNamespace(
        installer="pip", upstream="https://github.com/lbx154/Argus/archive/refs/heads/main.zip",
        source_note="Repository main channel.",
    ))
    assert update.run_update() == 0
    output = capsys.readouterr().out
    assert "package refreshed" in output
    assert "Repository main channel." in output
    assert "already up to date" not in output


def test_update_dispatch_reports_package_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(update, "source_root", lambda: tmp_path)

    def failed():
        raise update.UpdateError("installation failed")

    monkeypatch.setattr(package_update, "update_installed_package", failed)
    assert update.run_update() == 2
    output = capsys.readouterr()
    assert "installation failed" in output.err
    assert not output.out


def test_update_dispatch_keeps_source_path_and_reports_reinstall(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    monkeypatch.setattr(update, "source_root", lambda: tmp_path)
    monkeypatch.setattr(update, "update_source_checkout", lambda: update.UpdateResult(
        Path(tmp_path), "lbx154/Argus/main", "same", "same", installed=True,
    ))
    assert update.run_update() == 0
    assert "installation refreshed" in capsys.readouterr().out


def test_update_frozen_build_cannot_invoke_python_package_installer(monkeypatch, capsys):
    monkeypatch.setattr(update.sys, "frozen", True, raising=False)
    assert update.run_update() == 2
    assert "desktop updater" in capsys.readouterr().err
