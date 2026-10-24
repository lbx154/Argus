"""Argument-parser tests for the unified ``argus`` entry point.

The 7x24 pivot stripped legacy ``run`` and ``list-skills`` subcommands.
These tests pin down the surface so a future refactor cannot silently
re-introduce them; the idea-wiki admin path is the only supported
subcommand.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from argus.apps.cli import build_parser, main


def test_main_pins_pip_user_off_before_any_child_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI counterpart of the daemon boot pin: the default engineer spawn is
    dangerous_yolo, whose codex child inherits this process env untouched, so
    main() itself must neutralize pip's silent user-install fallback (the
    2026-09-05 launcher hijack) before any command runs. Dropping the
    configure_framework_python_env call from main must fail HERE."""
    monkeypatch.setenv("PIP_USER", "1")

    with pytest.raises(SystemExit, match="0"):
        main(["--version"])

    assert os.environ["PIP_USER"] == "0"


def test_debug_help_still_exposes_internal_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_DEBUG_HELP", "1")
    help_text = build_parser().format_help()
    assert "--daemon" in help_text
    assert "--status" in help_text
    assert "wiki" in help_text


def test_missing_repair_plan_fails_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main([
        "--life-dir", str(tmp_path),
        "repair", "--apply", "rp-20260814T000000Z-deadbeef", "--yes",
    ])

    captured = capsys.readouterr()
    assert rc == 3
    assert "repair refused" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("flag", ["--update", "-update"])
def test_update_flag_remains_exclusive_with_other_actions(
    flag: str, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    from argus.apps import update

    monkeypatch.setattr(
        update,
        "run_update",
        lambda: pytest.fail("conflicting actions must not run the updater"),
    )

    assert main([flag, "--status"]) == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_main_wiki_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.chdir(tmp_path)

    rc = main(["wiki", "init", "demo"])
    out = capsys.readouterr().out

    assert rc == 0
    wiki = tmp_path / ".autors" / "demo" / "wiki"
    assert (wiki / "pages").is_dir()
    assert (wiki / "INDEX.md").exists()
    assert not (wiki / "sources").exists()
    assert "wiki ready at" in out


def test_main_wiki_ingest_does_not_build_a_source_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    from argus.wiki.bootstrap import init_wiki

    wiki = init_wiki("demo", base=tmp_path)
    refs = tmp_path / "paper" / "refs.bib"
    refs.parent.mkdir()
    refs.write_text(
        """
@article{demo2026,
  title={Demo Paper},
  author={Doe, Jane},
  year={2026},
  url={https://arxiv.org/abs/2601.00001}
}
""",
        encoding="utf-8",
    )
    lit = tmp_path / "research" / "LIT_MATRIX.tsv"
    lit.parent.mkdir()
    lit.write_text(
        "id\tyear\ttype\tvenue\turl\trelevance_to_demo\n"
        "demo2026\t2026\trecent\tarXiv\thttps://arxiv.org/abs/2601.00001\tUseful.\n",
        encoding="utf-8",
    )

    rc = main(
        [
            "wiki",
            "ingest",
            "--wiki",
            str(wiki),
            "--refs",
            str(refs),
            "--lit-matrix",
            str(lit),
            "--ingested-by",
            "test",
        ]
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "ingested 0 new source(s)" in out
    assert "enriched 0 source(s)" in out
    assert not (wiki / "sources").exists()


def test_wiki_ingest_rejects_uninitialized_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    wiki = tmp_path / ".autors" / "demo" / "wiki"
    refs = tmp_path / "refs.bib"
    refs.write_text("@misc{x, title={x}}\n", encoding="utf-8")

    rc = main(["wiki", "ingest", "--wiki", str(wiki), "--refs", str(refs)])
    captured = capsys.readouterr()

    assert rc != 0
    assert "not an initialized wiki" in captured.err
    assert not wiki.exists()


def test_wiki_ingest_init_flag_bootstraps(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    wiki = tmp_path / ".autors" / "demo" / "wiki"
    refs = tmp_path / "refs.bib"
    refs.write_text(
        """
@misc{x,
  title={X},
  url={https://arxiv.org/abs/2601.00002}
}
""",
        encoding="utf-8",
    )

    rc = main(["wiki", "ingest", "--wiki", str(wiki), "--refs", str(refs), "--init"])
    out = capsys.readouterr().out

    assert rc == 0
    assert (wiki / "pages").is_dir()
    assert (wiki / "INDEX.md").exists()
    assert "ingested 0 new source(s)" in out


def test_web_uses_documented_flags_and_explicit_life_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_serve(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("argus.webapi.server.serve", fake_serve)

    assert main([
        "--web",
        "--no-open",
        "--host",
        "127.0.0.1",
        "--port",
        "8800",
        "--life-dir",
        str(tmp_path),
    ]) == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8800
    assert captured["global_root"] == tmp_path


def test_export_target_does_not_inherit_unrelated_cwd_vertical(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus.skills.vertical_select import persist_vertical

    caller = tmp_path / "caller"
    caller.mkdir()
    persist_vertical(caller, "software")
    monkeypatch.chdir(caller)
    target = tmp_path / "fresh-project" / "argus_builtin_skills"

    rc = main(["--export-builtin-skills", str(target)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "vertical: none (common skills only)" in out
    assert not (target / "engineer/paper-framework-figure-studio.md").exists()


def test_export_prunes_unmodified_vertical_skills_after_vertical_switch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from argus.skills.builtins import iter_vertical_skill_texts
    from argus.skills.vertical_select import persist_vertical

    project = tmp_path / "project"
    target = project / "argus_builtin_skills"
    maintenance_skills = {name for name, _text in iter_vertical_skill_texts("argus_maintenance")}
    persist_vertical(project, "argus_maintenance")
    assert main(["--export-builtin-skills", str(target), "--apply"]) == 0
    assert all((target / filename).is_file() for filename in maintenance_skills)
    capsys.readouterr()

    persist_vertical(project, "software")
    assert main(["--export-builtin-skills", str(target), "--apply"]) == 0
    out = capsys.readouterr().out

    assert not any((target / filename).exists() for filename in maintenance_skills)
    assert (
        f"pruned : {len(maintenance_skills)} inactive unmodified context seed(s)"
        in out
    )


def test_main_loads_objective_file_before_continuous_validation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    objective = tmp_path / "objective.txt"
    objective.write_text("Fix the Harbor task", encoding="utf-8")

    rc = main(["--objective-file", str(objective)])

    assert rc == 2
    assert "--objective requires --continuous" in capsys.readouterr().err


def test_main_reports_missing_objective_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.txt"

    rc = main(["--continuous", "--objective-file", str(missing)])

    assert rc == 2
    assert "could not read --objective-file" in capsys.readouterr().err


def test_main_rejects_continuous_without_objective(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--continuous"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--continuous requires a non-empty --objective" in err


def test_main_forwards_real_process_argv_to_ink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Console-script calls use ``main()``; argv=None must not erase flags."""
    captured: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["argus", "--resume", "s-session01"])

    def fake_run_tui(argv):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr("argus.apps.tui_launcher.main", fake_run_tui)

    assert main() == 0
    assert captured["argv"] == ["--resume", "s-session01"]


def test_wiki_ingest_init_flag_parses_without_abbreviation_collision():
    # Regression: top-level ``--init-identity`` / ``--init-model-api`` must not
    # turn the ``wiki ingest --init`` subcommand flag into an "ambiguous
    # option" on Python <= 3.12 (argparse pre-scans tokens against the parent
    # parser). The parent parser is built with ``allow_abbrev=False``.
    p = build_parser()
    args = p.parse_args(
        ["wiki", "ingest", "--wiki", "/tmp/w", "--refs", "/tmp/r.bib", "--init"]
    )
    assert args.command == "wiki"
    assert args.wiki_cmd == "ingest"
    assert args.init is True


def test_public_help_names_the_browser_cockpit() -> None:
    """`--web` is a first-class human surface, so it must be discoverable.

    The flag, its host/port options and a doctor check (ARGUS-WEB-001) all
    existed while the product-facing help never mentioned it, so the only way
    to learn the web UI exists was to read the source or set a debug env var.
    """
    help_text = build_parser().format_help()
    assert "argus --web" in help_text
    assert "argus --watch" in help_text


def test_a_missing_web_dependency_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The guard must fire before anything claims the server is up.

    `webapi.server` imports uvicorn lazily inside `serve()`, so guarding only
    the `webapi.server` import let the pairing banner print a URL and then a
    bare ImportError escape as a traceback — the message the guard exists to
    print was unreachable.
    """
    from argus.apps.cli import _core

    monkeypatch.setattr(
        _core, "_missing_web_dependency", lambda: "uvicorn"
    )

    assert main(["--web"]) == 2
    err = capsys.readouterr().err
    assert "uvicorn is missing" in err
    assert "Traceback" not in err
    assert "http://" not in err, "no URL may be offered when the server cannot start"


def test_a_negative_gc_window_is_refused_before_anything_moves(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI must name the mistake rather than let the sweep run.

    `argus --gc --gc-days -5` reached the collector, which trusted the value,
    and 50 projects were moved to trash on a real global root — 42 of them
    ones the same run's `--gc-dry-run` had not listed.
    """
    from argus.core import project_gc

    monkeypatch.setattr(
        project_gc,
        "gc_stale_projects",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not sweep")),
    )

    assert main(["--gc", "--gc-days", "-5"]) == 2
    err = capsys.readouterr().err
    assert "--gc-days must not be negative" in err
    assert "every project would be trashed" in err


@pytest.mark.parametrize("value", ["99999", "-1", "abc"])
def test_an_unbindable_web_port_is_refused_by_the_parser(value: str) -> None:
    """uvicorn would raise `OverflowError: bind(): port must be 0-65535`.

    It raised it *after* the pairing banner had already printed a URL on that
    port, so the operator was offered an address that could never exist and
    then shown a traceback.
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--web", "--web-port", value])


def test_mission_width_accepts_a_count_and_reads_retired_auto_as_default() -> None:
    parser = build_parser()
    assert parser.parse_args(["--daemon", "--mission-width", "auto"]).mission_width == 2
    assert parser.parse_args(["--daemon", "--mission-width", "3"]).mission_width == 3
    assert parser.parse_args(["--daemon"]).mission_width == 2
    with pytest.raises(SystemExit):
        parser.parse_args(["--daemon", "--mission-width", "-1"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--daemon", "--mission-width", "many"])


def test_wiki_correct_command_rewrites_the_page_and_journals_it(tmp_path, monkeypatch, capsys):
    from argus.wiki.journal import read_knowledge_events

    home = tmp_path / "home"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    library = home / "wiki" / "_shared_verticals" / "research"
    page = library / "pages" / "lessons" / "regime.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\ntitle: Regime\ndescription: Trust the control file\nkind: lesson\n---\n\n# Regime\n\nTrust it.\n",
        encoding="utf-8",
    )
    code = main([
        "wiki", "correct", "pages/lessons/regime.md", "--scope", "vertical", "--vertical", "research",
        "--statement", "Recompute the regime from timings.", "--reason", "analysis.py recomputes it.",
        "--description", "Recompute the regime", "--by", "reviewer",
    ])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "corrected pages/lessons/regime.md (Regime)" in out and "previously: Trust it." in out
    text = page.read_text(encoding="utf-8")
    assert "description: Recompute the regime" in text and ", corrected by reviewer: " in text
    assert text.index("Recompute the regime from timings.") < text.index("## History")
    rows = read_knowledge_events(home, kinds=["corrected"])
    assert len(rows) == 1 and rows[0]["scope"] == "vertical" and rows[0]["vertical"] == "research"
    assert rows[0]["role"] == "reviewer"
    assert main([
        "wiki", "correct", "pages/lessons/none.md", "--scope", "vertical", "--vertical", "research",
        "--statement", "s", "--reason", "r",
    ]) == 2


def test_no_operator_flag_declares_a_headless_run(monkeypatch) -> None:
    from argus.apps.cli import _core

    args = build_parser().parse_args(["--daemon-fg", "--bounded", "--no-operator"])
    assert args.no_operator is True
    assert build_parser().parse_args(["--daemon-fg", "--bounded"]).no_operator is False

    # Registered with monkeypatch so the variable main() sets is restored.
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")

    class _Stop(Exception):
        pass

    def _stop_after_parse(*_args, **_kwargs):
        raise _Stop

    # Stop right after the flag is applied; nothing else in main() matters here.
    monkeypatch.setattr("argus.core.knobs.resolve_role_backend", _stop_after_parse)
    with pytest.raises(_Stop):
        _core.main(["--status", "--no-operator"])
    assert os.environ["ARGUS_SKILL_OPERATOR_AVAILABLE"] == "false"
