"""Tests for the `argus start` command — parsing + pass-through forwarding."""
from __future__ import annotations

from argus_skill.apps.cli import _core
from argus_skill.apps.cli._parser import build_parser


def test_start_parser_takes_objective_and_collects_rest():
    args = build_parser().parse_args(
        ["start", "study X for EMNLP", "--venue", "aaai", "--dry-run"]
    )
    assert args.command == "start"
    assert args.goal == "study X for EMNLP"
    assert args.rest == ["--venue", "aaai", "--dry-run"]


def test_cmd_start_forwards_objective_then_rest(monkeypatch):
    captured = {}

    def fake_main(argv=None):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(
        "argus_skill.tools.new_auto_research_project.main", fake_main
    )
    args = build_parser().parse_args(
        ["start", "minimize val_bpb", "--venue", "aaai", "--no-start"]
    )
    rc = _core._cmd_start(args)
    assert rc == 0
    # objective becomes --objective, every extra flag passes through verbatim
    assert captured["argv"] == [
        "--objective", "minimize val_bpb", "--venue", "aaai", "--no-start",
    ]


def test_cmd_start_default_scaffolds_only_injects_no_start(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "argus_skill.tools.new_auto_research_project.main",
        lambda argv=None: captured.setdefault("argv", argv) or 0,
    )
    args = build_parser().parse_args(["start", "write a survey"])
    _core._cmd_start(args)
    # DEFAULT = scaffold only: --no-start is injected so the daemon does NOT launch
    assert captured["argv"] == ["--objective", "write a survey", "--no-start"]


def test_cmd_start_with_start_flag_launches(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "argus_skill.tools.new_auto_research_project.main",
        lambda argv=None: captured.setdefault("argv", argv) or 0,
    )
    args = build_parser().parse_args(["start", "write a survey", "--start"])
    _core._cmd_start(args)
    # --start opts back in: neither --start nor --no-start reaches the tool,
    # so it launches the daemon (its own default)
    assert captured["argv"] == ["--objective", "write a survey"]
