"""Argument-parser tests for the unified ``argus-skill`` entry point.

The 7x24 pivot stripped legacy ``run`` and ``list-skills`` subcommands.
These tests pin down the surface so a future refactor cannot silently
re-introduce them; the idea-wiki admin path is the only supported
subcommand.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.apps.cli import build_parser, main


def test_parser_has_only_wiki_subcommand():
    p = build_parser()
    args = p.parse_args(["wiki", "init", "demo"])
    assert args.command == "wiki"
    assert args.wiki_cmd == "init"
    assert args.project == "demo"


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
    assert (tmp_path / ".autors" / "demo" / "wiki" / "data" / "schema.yaml").exists()
    assert (tmp_path / ".autors" / "demo" / "wiki" / "query_pack.md").exists()
    assert "wiki ready at" in out


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
    assert (target / "engineer/auto-research-pipeline.md").exists()
    assert (target / "engineer/emnlp-paper-drafting.md").exists()
    assert (target / "engineer/arxiv-paper-search.md").exists()
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


def _seed_trusted_special_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create one trusted operator directive so the lifetime entry gate passes.

    chmod 0644 is required because the trust check rejects group/world-writable
    files (the sandbox umask otherwise yields 0664).
    """
    sp = tmp_path / "special_prompts"
    sp.mkdir()
    f = sp / "10-house-rules.md"
    f.write_text("Operational house rules for this box.\n", encoding="utf-8")
    f.chmod(0o644)
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(sp))


def test_main_seeds_repl_continuous_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "codex")
    _seed_trusted_special_prompt(tmp_path, monkeypatch)

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


def test_main_rejects_launch_without_objective(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Default REPL launch is gated: no objective -> refuse with guidance."""
    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "codex")
    _seed_trusted_special_prompt(tmp_path, monkeypatch)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))

    called = {"hit": False}

    def fake_run_life_chat_loop(args):  # pragma: no cover - must not run
        called["hit"] = True
        return 0

    monkeypatch.setattr("argus_skill.apps._life_repl.run_life_chat_loop", fake_run_life_chat_loop)

    rc = main([])
    assert rc == 2
    assert called["hit"] is False
    assert "no mission objective configured" in capsys.readouterr().err


def test_main_rejects_launch_without_special_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Default REPL launch is gated: no special prompt -> refuse with guidance."""
    monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "codex")
    # Point the special-prompts dir at an empty location so the gate trips.
    monkeypatch.setenv(
        "ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(tmp_path / "empty_special")
    )

    called = {"hit": False}

    def fake_run_life_chat_loop(args):  # pragma: no cover - must not run
        called["hit"] = True
        return 0

    monkeypatch.setattr("argus_skill.apps._life_repl.run_life_chat_loop", fake_run_life_chat_loop)

    rc = main(["--continuous", "--objective", "hardening objective"])
    assert rc == 2
    assert called["hit"] is False
    assert "special prompt" in capsys.readouterr().err.lower()
