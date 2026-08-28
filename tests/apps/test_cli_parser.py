"""Argument-parser tests for the unified ``argus-skill`` entry point.

The 7x24 pivot stripped legacy ``run`` and ``list-skills`` subcommands.
These tests pin down the surface so a future refactor cannot silently
re-introduce them; the idea-wiki admin path is the only supported
subcommand.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from argus_skill.apps.cli import build_parser, main


def test_public_help_distinguishes_human_and_automation_surfaces() -> None:
    help_text = build_parser().format_help()
    assert "usage: argus" in help_text
    assert "Then type what you need in natural language." in help_text
    assert "argus --daemon-fg" in help_text
    assert "supervised foreground worker" in help_text
    assert "argus --daemon" in help_text
    assert "persistent unattended background worker" in help_text
    assert "argus doctor" in help_text
    assert "argus repair --plan" in help_text
    assert "argus update" in help_text
    assert "--status" not in help_text
    assert "dashboard" not in help_text.lower()
    assert "wiki" not in help_text


def test_version_reports_package_version(capsys) -> None:
    with pytest.raises(SystemExit, match="0"):
        build_parser().parse_args(["--version"])
    rendered = capsys.readouterr().out
    assert rendered == "argus-skill 0.1.2\n"


def test_debug_help_still_exposes_internal_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_DEBUG_HELP", "1")
    help_text = build_parser().format_help()
    assert "--daemon" in help_text
    assert "--status" in help_text
    assert "wiki" in help_text


def test_parser_exposes_doctor_and_repair_subcommands() -> None:
    doctor = build_parser().parse_args(
        ["doctor", "--json", "--deep", "--advisor", "claude"]
    )
    assert doctor.command == "doctor"
    assert doctor.json is True
    assert doctor.deep is True
    assert doctor.advisor == "claude"
    for backend in ("qoder", "dsh"):
        parsed = build_parser().parse_args(["doctor", "--advisor", backend])
        assert parsed.advisor == backend

    repair = build_parser().parse_args(["repair", "--safe", "--json"])
    assert repair.command == "repair"
    assert repair.safe is True
    assert repair.json is True


def test_parser_accepts_doctor_safe_fix_and_repair_lifecycle() -> None:
    doctor = build_parser().parse_args(["doctor", "--fix-safe", "--json"])
    legacy = build_parser().parse_args(["-doctor", "--fix-safe", "--json"])
    apply = build_parser().parse_args(["repair", "--apply", "rp-20260814T000000Z-abc12345", "--yes"])
    prepare = build_parser().parse_args(["repair", "--prepare-pr", "rp-20260814T000000Z-abc12345"])
    submit = build_parser().parse_args(["repair", "--submit-pr", "rp-20260814T000000Z-abc12345", "--yes"])

    assert doctor.command == "doctor" and doctor.fix_safe is True
    assert legacy.doctor is True and legacy.fix_safe is True and legacy.json is True
    assert apply.apply == "rp-20260814T000000Z-abc12345" and apply.yes is True
    assert prepare.prepare_pr == "rp-20260814T000000Z-abc12345"
    assert submit.submit_pr == "rp-20260814T000000Z-abc12345" and submit.yes is True


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


def test_parser_exposes_update_subcommand():
    args = build_parser().parse_args(["update"])
    assert args.command == "update"


def test_parser_exposes_update_flag_alias():
    args = build_parser().parse_args(["--update"])
    assert args.update is True


def test_parser_has_wiki_subcommand():
    p = build_parser()
    args = p.parse_args(["wiki", "init", "demo"])
    assert args.command == "wiki"
    assert args.wiki_cmd == "init"
    assert args.project == "demo"


def test_parser_accepts_stage_targeted_notify() -> None:
    args = build_parser().parse_args([
        "--notify",
        "profile after certification",
        "--notify-stage",
        "optimize",
    ])
    assert args.notify == "profile after certification"
    assert args.notify_stage == "optimize"


def test_parser_accepts_ask_question() -> None:
    args = build_parser().parse_args(["--ask", "what is 2+2?"])
    assert args.ask == "what is 2+2?"


def test_parser_accepts_noninteractive_backend_setup_contract() -> None:
    args = build_parser().parse_args(
        [
            "--setup",
            "--non-interactive",
            "--backend",
            "codex",
            "--auth-mode",
            "subscription_cli",
            "--allow-prerelease",
        ]
    )

    assert args.setup is True
    assert args.non_interactive is True
    assert args.backend == "codex"
    assert args.auth_mode == "subscription_cli"
    assert args.allow_prerelease is True


def test_parser_accepts_pi_backend() -> None:
    args = build_parser().parse_args(["--doctor", "--backend", "pi"])
    assert args.backend == "pi"


def test_parser_accepts_grok_backend() -> None:
    args = build_parser().parse_args(["--doctor", "--backend", "grok"])
    assert args.backend == "grok"


def test_parser_accepts_qoder_backend() -> None:
    args = build_parser().parse_args(["--doctor", "--backend", "qoder"])
    assert args.backend == "qoder"


def test_parser_exposes_cli_doctor() -> None:
    args = build_parser().parse_args(["--doctor", "--backend", "copilot"])

    assert args.doctor is True
    assert args.backend == "copilot"


def test_parser_accepts_hidden_single_dash_doctor_alias() -> None:
    args = build_parser().parse_args(["-doctor"])
    assert args.doctor is True


def test_parser_accepts_wiki_ingest_subcommand(tmp_path: Path):
    p = build_parser()
    wiki = tmp_path / ".autors" / "demo" / "wiki"
    args = p.parse_args(["wiki", "ingest", "--wiki", str(wiki)])
    assert args.command == "wiki"
    assert args.wiki_cmd == "ingest"
    assert args.wiki == wiki
    assert args.ingested_by == "wiki-curator@manual-backfill"
    assert args.init is False


def test_parser_rejects_legacy_run_subcommand():
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["run"])


def test_parser_rejects_legacy_list_skills_subcommand():
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["list-skills"])


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
    from argus_skill.wiki.bootstrap import init_wiki

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


def test_parser_accepts_no_daemon_flag():
    p = build_parser()
    args = p.parse_args(["--no-daemon"])
    assert args.no_daemon is True


def test_parser_no_daemon_default_false():
    p = build_parser()
    args = p.parse_args([])
    assert args.no_daemon is False


def test_parser_accepts_documented_web_host_and_port_aliases():
    args = build_parser().parse_args([
        "--web",
        "--host",
        "0.0.0.0",
        "--port",
        "8800",
    ])
    assert args.web_host == "0.0.0.0"
    assert args.web_port == 8800


def test_web_uses_documented_flags_and_explicit_life_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_serve(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("argus_skill.webapi.server.serve", fake_serve)

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


def test_parser_daemon_flags_present():
    p = build_parser()
    for flag in ("--daemon", "--daemon-fg", "--daemon-stop", "--status", "--daemon-runbook"):
        args = p.parse_args([flag])
        # Each flag flips its own bool; nothing else.
        attr = flag.lstrip("-").replace("-", "_")
        assert getattr(args, attr) is True


def test_parser_model_api_flags_present():
    p = build_parser()
    assert p.parse_args(["--model-api-status"]).model_api_status is True
    assert p.parse_args(["--init-model-api"]).init_model_api is True


def test_parser_ppt_master_flags_present():
    p = build_parser()
    assert p.parse_args(["--install-ppt-master"]).install_ppt_master is True
    assert p.parse_args(["--ppt-master-status"]).ppt_master_status is True


def test_parser_export_builtin_skills_flag_present():
    p = build_parser()
    assert (
        p.parse_args(["--export-builtin-skills"]).export_builtin_skills
        == "argus_builtin_skills"
    )
    assert (
        p.parse_args(
            ["--export-builtin-skills", "project_skills"],
        ).export_builtin_skills
        == "project_skills"
    )


def test_main_exports_builtin_skills(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "project" / "argus_builtin_skills"

    rc = main(["--export-builtin-skills", str(target)])
    out = capsys.readouterr().out

    assert rc == 0
    assert (target / "engineer/argus-engineer-role.md").exists()
    assert (target / "engineer/semantic-scholar-search.md").exists()
    assert not (target / "engineer/auto-research-pipeline.md").exists()
    assert not (target / "engineer/emnlp-paper-drafting.md").exists()
    assert not (target / "engineer/arxiv-paper-search.md").exists()
    assert not (target / "engineer/research-visualization-router.md").exists()
    assert "exported built-in skills" in out
    assert "vertical: none (common skills only)" in out
    assert str(target) in out


def test_main_exports_decided_vertical_skills(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from argus_skill.skills.vertical_select import persist_vertical

    target = tmp_path / "project" / "argus_builtin_skills"
    persist_vertical(target.parent, "research")

    rc = main(["--export-builtin-skills", str(target)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "vertical: research" in out
    assert (target / "engineer/research-visualization-router.md").exists()
    assert (target / "engineer/auto-research-pipeline.md").exists()


def test_export_target_does_not_inherit_unrelated_cwd_vertical(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.skills.vertical_select import persist_vertical

    caller = tmp_path / "caller"
    caller.mkdir()
    persist_vertical(caller, "software")
    monkeypatch.chdir(caller)
    target = tmp_path / "fresh-project" / "argus_builtin_skills"

    rc = main(["--export-builtin-skills", str(target)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "vertical: none (common skills only)" in out
    assert not (target / "engineer/research-visualization-router.md").exists()


def test_export_prunes_legacy_unmodified_research_fallback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from argus_skill.skills.builtins import (
        iter_vertical_skill_texts,
        seed_vertical_skills,
    )

    target = tmp_path / "legacy-project" / "argus_builtin_skills"
    seed_vertical_skills(target, "research")
    assert (target / "engineer/research-visualization-router.md").exists()

    rc = main(["--export-builtin-skills", str(target), "--apply"])
    out = capsys.readouterr().out

    assert rc == 0
    assert not (target / "engineer/research-visualization-router.md").exists()
    assert not (
        target / "engineer/research_visual_scripts/browser_render.py"
    ).exists()
    expected = len(dict(iter_vertical_skill_texts("research")))
    assert f"pruned : {expected} inactive unmodified context seed(s)" in out


def test_export_preserves_edited_legacy_research_fallback(
    tmp_path: Path,
) -> None:
    from argus_skill.skills.builtins import seed_vertical_skills

    target = tmp_path / "learned-project" / "argus_builtin_skills"
    seed_vertical_skills(target, "research")
    router = target / "engineer/research-visualization-router.md"
    router.write_text(
        router.read_text(encoding="utf-8") + "\nproject-specific learning\n",
        encoding="utf-8",
    )

    assert main(["--export-builtin-skills", str(target), "--apply"]) == 0

    assert router.exists()
    assert "project-specific learning" in router.read_text(encoding="utf-8")
    assert not (
        target / "engineer/research_visual_scripts/browser_render.py"
    ).exists()


def test_export_prunes_unmodified_math_skills_after_vertical_switch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from argus_skill.skills.builtins import iter_vertical_skill_texts
    from argus_skill.skills.vertical_select import persist_vertical

    project = tmp_path / "project"
    target = project / "argus_builtin_skills"
    math_skills = {name for name, _text in iter_vertical_skill_texts("math")}
    persist_vertical(project, "math")
    assert main(["--export-builtin-skills", str(target), "--apply"]) == 0
    assert all((target / filename).is_file() for filename in math_skills)
    capsys.readouterr()

    persist_vertical(project, "software")
    assert main(["--export-builtin-skills", str(target), "--apply"]) == 0
    out = capsys.readouterr().out

    assert not any((target / filename).exists() for filename in math_skills)
    assert (
        f"pruned : {len(math_skills)} inactive unmodified context seed(s)"
        in out
    )


def test_main_rejects_objective_without_continuous(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--objective", "hardening objective"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--objective requires --continuous" in err


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


def test_main_rejects_continuous_on_memory_backend(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "memory")
    rc = main(["--continuous", "--objective", "hardening objective"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "cannot plan" in err


def test_main_rejects_continuous_on_memory_backend_for_daemon(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "memory")
    rc = main(["--daemon", "--continuous", "--objective", "hardening objective"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "cannot plan" in err


def test_main_rejects_continuous_on_persisted_memory_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from argus_skill.core.knob_store import write_persisted_knob

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    assert write_persisted_knob("ARGUS_SKILL_LIFE_BACKEND", "memory")

    rc = main(["--continuous", "--objective", "hardening objective"])

    err = capsys.readouterr().err
    assert rc == 2
    assert "cannot plan" in err


def test_main_forwards_continuous_objective_to_ink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "codex")

    captured: dict[str, object] = {}

    def fake_run_tui(argv):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr("argus_skill.apps.tui_launcher.main", fake_run_tui)

    rc = main(["--continuous", "--objective", "hardening objective"])

    assert rc == 0
    assert captured["argv"] == ["--continuous", "--objective", "hardening objective"]


def test_main_forwards_real_process_argv_to_ink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Console-script calls use ``main()``; argv=None must not erase flags."""
    captured: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["argus-skill", "--resume", "s-session01"])

    def fake_run_tui(argv):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr("argus_skill.apps.tui_launcher.main", fake_run_tui)

    assert main() == 0
    assert captured["argv"] == ["--resume", "s-session01"]


def test_main_bare_launch_enters_ink_without_objective(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare ``argus-skill`` enters the single supported Ink cockpit."""
    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "codex")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))

    called = {"hit": False}

    def fake_run_tui(argv):
        called["hit"] = True
        return 0

    monkeypatch.setattr("argus_skill.apps.tui_launcher.main", fake_run_tui)

    rc = main([])
    assert rc == 0
    assert called["hit"] is True


def test_main_ink_launch_allows_empty_special_prompt_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cockpit startup does not require machine-specific prompt files."""
    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "codex")
    monkeypatch.setenv(
        "ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(tmp_path / "empty_special")
    )

    called = {"hit": False}

    def fake_run_tui(argv):
        called["hit"] = True
        return 0

    monkeypatch.setattr("argus_skill.apps.tui_launcher.main", fake_run_tui)

    rc = main(["--continuous", "--objective", "hardening objective"])
    assert rc == 0
    assert called["hit"] is True


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


def test_top_level_abbreviation_is_disabled():
    # ``allow_abbrev=False`` means abbreviated top-level flags are rejected
    # rather than silently expanded — this is what prevents the ``--init``
    # ambiguity from re-appearing as new ``--init-*`` flags are added.
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["--objec", "x"])


def test_public_help_names_the_browser_cockpit() -> None:
    """`--web` is a first-class human surface, so it must be discoverable.

    The flag, its host/port options and a doctor check (ARGUS-WEB-001) all
    existed while the product-facing help never mentioned it, so the only way
    to learn the web UI exists was to read the source or set a debug env var.
    """
    help_text = build_parser().format_help()
    assert "argus --web" in help_text
    assert "argus --watch" in help_text


def test_every_supported_backend_is_selectable_and_documented() -> None:
    """One list, three renderings.

    The backend set was written out by hand in the `--backend` choices, the
    `--advisor` choices, the readiness check and the operator knob help. The
    knob help had drifted to five of the eight, so `argus --config-help` — the
    documented operator control surface — hid three backends the CLI accepts.
    """
    from argus_skill.agent_cli.runner_backend import SUPPORTED_BACKENDS
    from argus_skill.core.backend_readiness import _SUPPORTED_BACKENDS
    from argus_skill.core.knobs import format_config_help

    assert set(SUPPORTED_BACKENDS) == set(_SUPPORTED_BACKENDS)

    actions = {action.dest: action for action in build_parser()._actions}
    assert set(actions["backend"].choices) == set(SUPPORTED_BACKENDS)

    backend_line = next(
        line for line in format_config_help().splitlines()
        if "agent backend:" in line
    )
    for backend in SUPPORTED_BACKENDS:
        assert backend in backend_line, f"{backend} is selectable but undocumented"


def test_config_help_reports_the_backend_selected_by_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.core.knob_store import write_persisted_knob
    from argus_skill.core.knobs import format_config_help

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    write_persisted_knob("ARGUS_SKILL_RUNNER_BACKEND", "pi")

    help_text = format_config_help(env={})

    assert (
        "ARGUS_SKILL_RUNNER_BACKEND  (default: codex)  = pi (persisted)"
        in help_text
    )
    assert "ARGUS_SKILL_ENGINEER_BACKEND  (default: (=RUNNER_BACKEND))" in help_text


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
    from argus_skill.apps.cli import _core

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
    from argus_skill.core import project_gc

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


@pytest.mark.parametrize("value", ["0", "8799", "65535"])
def test_a_bindable_web_port_is_accepted(value: str) -> None:
    """Zero is legal: it asks the kernel for any free port."""
    args = build_parser().parse_args(["--web", "--web-port", value])
    assert args.web_port == int(value)
