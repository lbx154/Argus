"""Argument-parser tests for the unified ``argus-skill`` entry point.

The 7×24 pivot stripped ``run`` and ``list-skills`` subcommands. These
tests pin down the surface so a future refactor cannot silently
re-introduce them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.apps.cli import build_parser, main


def test_parser_has_no_subcommands():
    p = build_parser()
    actions = p._actions  # noqa: SLF001
    has_subparsers = any(
        action.__class__.__name__ == "_SubParsersAction" for action in actions
    )
    assert not has_subparsers, (
        "argus-skill is a single 7×24 entry point — no subcommands."
    )


def test_parser_rejects_legacy_run_subcommand():
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["run"])


def test_parser_rejects_legacy_list_skills_subcommand():
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["list-skills"])


def test_parser_accepts_no_daemon_flag():
    p = build_parser()
    args = p.parse_args(["--no-daemon"])
    assert args.no_daemon is True


def test_parser_no_daemon_default_false():
    p = build_parser()
    args = p.parse_args([])
    assert args.no_daemon is False


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
    assert (target / "auto-research-pipeline.md").exists()
    assert (target / "emnlp-paper-drafting.md").exists()
    assert (target / "domains" / "agents-rag" / "langchain.md").exists()
    assert "exported built-in skills" in out
    assert str(target) in out


def test_main_rejects_objective_without_continuous(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--objective", "hardening objective"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--objective requires --continuous" in err


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


def test_main_seeds_repl_continuous_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "codex")

    captured: dict[str, object] = {}

    def fake_run_life_chat_loop(args):
        captured["backend"] = args.backend
        captured["continuous"] = args.continuous
        captured["objective"] = args.objective
        return 0

    monkeypatch.setattr("argus_skill.apps._life_repl.run_life_chat_loop", fake_run_life_chat_loop)

    rc = main(["--continuous", "--objective", "hardening objective"])

    assert rc == 0
    assert captured == {
        "backend": "codex",
        "continuous": True,
        "objective": "hardening objective",
    }
