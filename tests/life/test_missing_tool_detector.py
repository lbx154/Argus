"""Tests for argus_skill.life.missing_tool_detector."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.life.missing_tool_detector import (
    MissingToolSignal,
    scan_events_jsonl,
    scan_mission,
    scan_text,
)


# ---------------------------------------------------------------------------
# Shell command-not-found patterns
# ---------------------------------------------------------------------------


def test_bash_command_not_found_basic() -> None:
    text = "/bin/bash: line 1: pdftotext: command not found\n"
    signals = scan_text(text)
    names = {s.tool_name for s in signals}
    assert "pdftotext" in names


def test_bash_no_such_file_binary() -> None:
    text = "/bin/bash: line 3: /usr/local/bin/ocrmypdf: No such file or directory\n"
    signals = scan_text(text)
    assert any(s.tool_name == "ocrmypdf" or "ocrmypdf" in s.tool_name for s in signals)


def test_multiple_distinct_commands_each_surface() -> None:
    text = (
        "/bin/bash: line 1: pdftotext: command not found\n"
        "/bin/bash: line 2: convert: command not found\n"
    )
    signals = scan_text(text)
    names = {s.tool_name for s in signals}
    assert "pdftotext" in names
    assert "convert" in names


# ---------------------------------------------------------------------------
# Python module not found
# ---------------------------------------------------------------------------


def test_python_module_not_found_basic() -> None:
    text = "Traceback ...\nModuleNotFoundError: No module named 'pdfplumber'\n"
    signals = scan_text(text)
    names = {s.tool_name for s in signals}
    assert "pdfplumber" in names
    assert any(s.kind == "python_module" for s in signals)


def test_python_module_keeps_top_level_package() -> None:
    # "torch.distributed.fsdp" → we want "torch" (top-level)
    text = "ModuleNotFoundError: No module named 'torch.distributed.fsdp'"
    signals = scan_text(text)
    names = {s.tool_name for s in signals}
    assert "torch" in names


def test_python_importerror_name() -> None:
    text = "ImportError: cannot import name 'FooBar' from 'baz'"
    signals = scan_text(text)
    names = {s.tool_name for s in signals}
    assert "foobar" in names


# ---------------------------------------------------------------------------
# Self-report
# ---------------------------------------------------------------------------


def test_engineer_self_report_english() -> None:
    text = "I don't have a tool to render mermaid diagrams to PNG."
    signals = scan_text(text)
    assert any(s.kind == "self_report" for s in signals)
    assert any("mermaid" in s.tool_name for s in signals)


def test_engineer_self_report_chinese() -> None:
    text = "我没有对应的工具来跑这个 docker 容器"
    signals = scan_text(text)
    assert any(s.kind == "self_report" for s in signals)


def test_engineer_would_need_function() -> None:
    text = "I would need a function for extracting text from PDF screenshots."
    signals = scan_text(text)
    assert any(s.kind == "self_report" for s in signals)


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def test_same_tool_from_two_patterns_dedups() -> None:
    text = (
        "/bin/bash: line 1: pdfplumber: command not found\n"
        "ModuleNotFoundError: No module named 'pdfplumber'\n"
    )
    signals = scan_text(text)
    # Both detectors find "pdfplumber"; dedup keeps one.
    names = [s.tool_name for s in signals]
    assert names.count("pdfplumber") == 1


def test_empty_text_returns_empty() -> None:
    assert scan_text("") == []


def test_clean_output_returns_empty() -> None:
    assert scan_text("$ pdftotext input.pdf output.txt\n$ ls\noutput.txt\n") == []


# ---------------------------------------------------------------------------
# scan_mission — multi-source aggregation
# ---------------------------------------------------------------------------


def test_scan_mission_collects_from_all_sources() -> None:
    signals = scan_mission(
        agent_messages=["I would need a tool to render LaTeX equations."],
        check_output_tails=["/bin/bash: line 1: pdftotext: command not found"],
        fatal_error="ModuleNotFoundError: No module named 'wandb'",
        events=[
            {"output_excerpt": "/bin/bash: line 1: convert: command not found",
             "exit_code": 127, "text": "convert input.jpg output.png"},
        ],
    )
    names = {s.tool_name for s in signals}
    # All four sources surfaced their distinct missing tool.
    assert "pdftotext" in names
    assert "wandb" in names
    assert "convert" in names
    assert any("latex" in n or "render" in n for n in names)


def test_scan_mission_dedups_across_sources() -> None:
    signals = scan_mission(
        agent_messages=["ModuleNotFoundError: No module named 'wandb'"],
        check_output_tails=["ModuleNotFoundError: No module named 'wandb'"],
    )
    names = [s.tool_name for s in signals]
    assert names.count("wandb") == 1


def test_scan_mission_exit_code_127_surface_command() -> None:
    signals = scan_mission(events=[
        {"exit_code": 127, "text": "pdftotext --version"}
    ])
    names = {s.tool_name for s in signals}
    assert "pdftotext" in names


def test_scan_mission_empty_inputs_safe() -> None:
    assert scan_mission() == []


# ---------------------------------------------------------------------------
# scan_events_jsonl — real-shape events
# ---------------------------------------------------------------------------


def test_scan_events_jsonl_against_real_shape(tmp_path: Path) -> None:
    events = [
        # planner.start — no signal
        {"type": "life.planner.start", "cycle": 0, "objective": "..."},
        # successful command — no signal
        {"type": "engineer.progress", "kind": "command_execution",
         "text": "/bin/bash -lc \"ls\"", "exit_code": 0,
         "output_excerpt": "AGENTS.md\nresearch/"},
        # missing binary
        {"type": "engineer.progress", "kind": "command_execution",
         "text": "/bin/bash -lc \"pdftotext input.pdf out.txt\"",
         "exit_code": 127,
         "output_excerpt": "/bin/bash: pdftotext: command not found"},
        # missing python module
        {"type": "engineer.progress", "kind": "command_execution",
         "text": "/bin/bash -lc \"python -c 'import pdfplumber'\"",
         "exit_code": 1,
         "output_excerpt": "ModuleNotFoundError: No module named 'pdfplumber'"},
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    signals = scan_events_jsonl(path)
    names = {s.tool_name for s in signals}
    assert "pdftotext" in names
    assert "pdfplumber" in names


def test_scan_events_jsonl_missing_file_returns_empty(tmp_path: Path) -> None:
    assert scan_events_jsonl(tmp_path / "nonexistent.jsonl") == []


def test_scan_events_jsonl_tolerates_bad_lines(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"valid": "json"}\n'
        'this is not json\n'
        '{"exit_code": 127, "text": "missing-tool"}\n',
        encoding="utf-8",
    )
    signals = scan_events_jsonl(path)
    # The bad line is skipped, the valid 127 line still surfaces.
    names = {s.tool_name for s in signals}
    assert "missing-tool" in names


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_human_output(tmp_path: Path, capsys) -> None:
    events = [
        {"exit_code": 127, "text": "convert image.png"}
    ]
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(events[0]) + "\n", encoding="utf-8")

    from argus_skill.life.missing_tool_detector import main as _main
    rc = _main(["--events", str(path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "missing_tool_detector:" in out
    assert "convert" in out


def test_cli_json_output(tmp_path: Path, capsys) -> None:
    events = [
        {"exit_code": 127, "text": "convert image.png"}
    ]
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(events[0]) + "\n", encoding="utf-8")

    from argus_skill.life.missing_tool_detector import main as _main
    rc = _main(["--events", str(path), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert any(s["tool_name"] == "convert" for s in payload)
