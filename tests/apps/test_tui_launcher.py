from pathlib import Path

from argus_skill.apps import tui_launcher


def test_launcher_execs_node_with_bundled_ink(monkeypatch, tmp_path: Path) -> None:
    bundle = tmp_path / "argus.mjs"
    bundle.write_text("// bundle", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(tui_launcher, "_bundle_path", lambda: bundle)
    monkeypatch.setattr(tui_launcher.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(tui_launcher, "_node_major", lambda node: 20)
    monkeypatch.setattr(
        tui_launcher.os,
        "execv",
        lambda executable, argv: seen.update(executable=executable, argv=argv),
    )

    assert tui_launcher.main(["--project", "s-test"]) == 0
    assert seen["executable"] == "/usr/bin/node"
    assert seen["argv"] == ["/usr/bin/node", str(bundle), "--project", "s-test"]


def test_launcher_fails_cleanly_without_bundle(monkeypatch, capsys) -> None:
    monkeypatch.setattr(tui_launcher, "_bundle_path", lambda: None)
    assert tui_launcher.main([]) == 2
    assert "bundled Ink TUI is missing" in capsys.readouterr().err


def test_launcher_rejects_unsupported_node(monkeypatch, tmp_path: Path, capsys) -> None:
    bundle = tmp_path / "argus.mjs"
    bundle.write_text("// bundle", encoding="utf-8")
    monkeypatch.setattr(tui_launcher, "_bundle_path", lambda: bundle)
    monkeypatch.setattr(tui_launcher.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(tui_launcher, "_node_major", lambda node: 16)

    assert tui_launcher.main([]) == 2
    assert "found 16" in capsys.readouterr().err


def test_report_subcommand_stays_on_python_admin_path(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(
        tui_launcher,
        "_run_python_admin",
        lambda argv: seen.append(argv) or 7,
    )
    monkeypatch.setattr(
        tui_launcher,
        "_bundle_path",
        lambda: (_ for _ in ()).throw(AssertionError("TUI must not launch")),
    )
    assert tui_launcher.main(["report", "metric", "--name", "score"]) == 7
    assert seen == [["report", "metric", "--name", "score"]]
