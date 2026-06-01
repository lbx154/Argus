"""Tests for argus_skill.skills.mint_skill_validator."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from argus_skill.skills.mint_skill_validator import (
    MIN_FIXTURE_CASES,
    main as validator_main,
    validate_candidate_skill,
)


# ---------------------------------------------------------------------------
# Helpers — build a minimal skill + fixture tree
# ---------------------------------------------------------------------------


def _seed_skill(root: Path, slug: str, script_body: str) -> Path:
    skill = root / f"{slug}.md"
    skill.write_text(
        f"---\nname: {slug}\ndescription: test skill\n---\n# test\n",
        encoding="utf-8",
    )
    scripts = root / f"{slug}_scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    main = scripts / "main.py"
    main.write_text(script_body, encoding="utf-8")
    return skill


def _seed_fixture(root: Path, case: str, input_text: str, expected_text: str,
                  ext: str = ".txt") -> None:
    d = root / case
    d.mkdir(parents=True, exist_ok=True)
    (d / f"input{ext}").write_text(input_text, encoding="utf-8")
    (d / f"expected{ext}").write_text(expected_text, encoding="utf-8")


# Echo script for happy path tests.
_ECHO_SCRIPT = """\
import sys
sys.stdout.write(sys.stdin.read())
"""

# Uppercase script for transform tests.
_UPPER_SCRIPT = """\
import sys
sys.stdout.write(sys.stdin.read().upper())
"""


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_validator_passes_when_all_fixtures_match(tmp_path: Path) -> None:
    skill_root = tmp_path / "skill_root"
    skill_root.mkdir()
    skill = _seed_skill(skill_root, "echo-skill", _ECHO_SCRIPT)
    fixtures = tmp_path / "fixtures"
    for i in range(1, 4):
        _seed_fixture(fixtures, f"case_{i:03d}", f"hello {i}\n", f"hello {i}\n")

    report = validate_candidate_skill(skill, fixtures)
    assert report.ok, report.to_dict()
    assert report.total == 3
    assert report.passed_count == 3


def test_validator_passes_with_uppercase_transform(tmp_path: Path) -> None:
    skill_root = tmp_path / "skill_root"
    skill_root.mkdir()
    skill = _seed_skill(skill_root, "upper-skill", _UPPER_SCRIPT)
    fixtures = tmp_path / "fixtures"
    cases = [("hello", "HELLO"), ("foo bar", "FOO BAR"), ("argus", "ARGUS")]
    for i, (i_text, e_text) in enumerate(cases, 1):
        _seed_fixture(fixtures, f"case_{i:03d}", i_text, e_text)

    report = validate_candidate_skill(skill, fixtures)
    assert report.ok


def test_validator_tolerates_trailing_newline_diff(tmp_path: Path) -> None:
    skill_root = tmp_path / "skill_root"
    skill_root.mkdir()
    skill = _seed_skill(skill_root, "echo-skill", _ECHO_SCRIPT)
    fixtures = tmp_path / "fixtures"
    # Inputs end with newline, expected without — should still pass.
    for i in range(1, 4):
        _seed_fixture(fixtures, f"case_{i:03d}", f"hello {i}\n", f"hello {i}")

    report = validate_candidate_skill(skill, fixtures)
    assert report.ok


def test_validator_json_comparison(tmp_path: Path) -> None:
    skill_root = tmp_path / "skill_root"
    skill_root.mkdir()
    json_passthrough = """\
import sys, json
data = json.load(sys.stdin)
json.dump(data, sys.stdout)
"""
    skill = _seed_skill(skill_root, "json-skill", json_passthrough)
    fixtures = tmp_path / "fixtures"
    for i in range(1, 4):
        _seed_fixture(
            fixtures, f"case_{i:03d}",
            json.dumps({"i": i, "n": i * 10}),
            # different key order — json equality should still pass
            json.dumps({"n": i * 10, "i": i}),
            ext=".json",
        )
    report = validate_candidate_skill(skill, fixtures)
    assert report.ok


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_validator_fails_when_output_differs(tmp_path: Path) -> None:
    skill_root = tmp_path / "skill_root"
    skill_root.mkdir()
    skill = _seed_skill(skill_root, "echo-skill", _ECHO_SCRIPT)
    fixtures = tmp_path / "fixtures"
    _seed_fixture(fixtures, "case_001", "hello", "hello")
    _seed_fixture(fixtures, "case_002", "world", "HELLO_NOT_WORLD")  # mismatch
    _seed_fixture(fixtures, "case_003", "foo", "foo")

    report = validate_candidate_skill(skill, fixtures)
    assert not report.ok
    assert report.passed_count == 2
    assert any("differs" in c.detail for c in report.cases if not c.passed)


def test_validator_fails_when_script_crashes(tmp_path: Path) -> None:
    skill_root = tmp_path / "skill_root"
    skill_root.mkdir()
    crash_script = "import sys; sys.exit(2)\n"
    skill = _seed_skill(skill_root, "crash-skill", crash_script)
    fixtures = tmp_path / "fixtures"
    for i in range(1, 4):
        _seed_fixture(fixtures, f"case_{i:03d}", "x", "x")

    report = validate_candidate_skill(skill, fixtures)
    assert not report.ok
    assert all("exited 2" in c.detail for c in report.cases)


def test_validator_fails_when_too_few_fixtures(tmp_path: Path) -> None:
    skill_root = tmp_path / "skill_root"
    skill_root.mkdir()
    skill = _seed_skill(skill_root, "echo-skill", _ECHO_SCRIPT)
    fixtures = tmp_path / "fixtures"
    _seed_fixture(fixtures, "case_001", "x", "x")
    # Only 1 case; MIN_FIXTURE_CASES is 3.

    report = validate_candidate_skill(skill, fixtures)
    assert not report.ok
    assert any("at least" in e and "fixture" in e for e in report.structural_errors)


def test_validator_fails_when_skill_missing(tmp_path: Path) -> None:
    report = validate_candidate_skill(
        tmp_path / "nonexistent.md", tmp_path / "fixtures"
    )
    assert not report.ok
    assert any("skill markdown not found" in e for e in report.structural_errors)


def test_validator_fails_when_script_missing(tmp_path: Path) -> None:
    skill = tmp_path / "noscript.md"
    skill.write_text("---\nname: x\ndescription: y\n---\n", encoding="utf-8")
    # Don't create scripts dir
    report = validate_candidate_skill(skill, tmp_path / "fixtures")
    assert not report.ok
    assert any("script not found" in e for e in report.structural_errors)


def test_validator_flags_malformed_fixture_dir(tmp_path: Path) -> None:
    skill_root = tmp_path / "skill_root"
    skill_root.mkdir()
    skill = _seed_skill(skill_root, "echo-skill", _ECHO_SCRIPT)
    fixtures = tmp_path / "fixtures"
    # Case with two input files → structural error
    case = fixtures / "bad_case"
    case.mkdir(parents=True)
    (case / "input.txt").write_text("x", encoding="utf-8")
    (case / "input.json").write_text("y", encoding="utf-8")
    (case / "expected.txt").write_text("z", encoding="utf-8")
    # Also seed 3 valid cases so we don't trip the count gate.
    for i in range(1, 4):
        _seed_fixture(fixtures, f"case_{i:03d}", "a", "a")

    report = validate_candidate_skill(skill, fixtures)
    assert any("need exactly one" in e for e in report.structural_errors)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_exits_zero_on_pass(tmp_path: Path, capsys) -> None:
    skill_root = tmp_path / "skill_root"
    skill_root.mkdir()
    skill = _seed_skill(skill_root, "echo-skill", _ECHO_SCRIPT)
    fixtures = tmp_path / "fixtures"
    for i in range(1, 4):
        _seed_fixture(fixtures, f"case_{i:03d}", f"x{i}", f"x{i}")

    rc = validator_main([
        "--skill", str(skill),
        "--fixtures", str(fixtures),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out


def test_cli_exits_nonzero_on_fail(tmp_path: Path, capsys) -> None:
    skill_root = tmp_path / "skill_root"
    skill_root.mkdir()
    skill = _seed_skill(skill_root, "echo-skill", _ECHO_SCRIPT)
    fixtures = tmp_path / "fixtures"
    _seed_fixture(fixtures, "case_001", "a", "WRONG")
    _seed_fixture(fixtures, "case_002", "b", "WRONG")
    _seed_fixture(fixtures, "case_003", "c", "WRONG")

    rc = validator_main([
        "--skill", str(skill),
        "--fixtures", str(fixtures),
    ])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL" in out


def test_cli_json_payload(tmp_path: Path, capsys) -> None:
    skill_root = tmp_path / "skill_root"
    skill_root.mkdir()
    skill = _seed_skill(skill_root, "echo-skill", _ECHO_SCRIPT)
    fixtures = tmp_path / "fixtures"
    for i in range(1, 4):
        _seed_fixture(fixtures, f"case_{i:03d}", f"x{i}", f"x{i}")

    rc = validator_main([
        "--skill", str(skill),
        "--fixtures", str(fixtures),
        "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["passed"] == 3
    assert payload["total"] == 3


def test_cli_min_fixture_cases_constant_exposed() -> None:
    # The minimum is part of the public contract; mint-skill prompt
    # tells the agent to write at least this many cases.
    assert MIN_FIXTURE_CASES == 3
