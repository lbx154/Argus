from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.tools.lean_check import (
    DIVISIBILITY_SMOKE_THEOREM,
    find_proof_holes,
    main,
    run_lean_check,
)


def _fake_lean(tmp_path: Path, behavior: str) -> Path:
    path = tmp_path / f"lean-{behavior}"
    bodies = {
        "success": "raise SystemExit(0)",
        "syntax": (
            "import sys; print('unexpected token', file=sys.stderr); "
            "raise SystemExit(1)"
        ),
        "type": (
            "import sys; print('type mismatch', file=sys.stderr); "
            "raise SystemExit(1)"
        ),
        "timeout": "import time; time.sleep(5)",
        "warning": "print(\"warning: declaration uses 'sorry'\"); raise SystemExit(0)",
        "meta-axiom": (
            "import sys; "
            "print('ARGUS_AXIOM_AUDIT_FOUND: forged', file=sys.stderr) "
            "if '--run' in sys.argv else None; "
            "raise SystemExit(3 if '--run' in sys.argv else 0)"
        ),
    }
    path.write_text(
        "#!/usr/bin/env python3\n" + bodies[behavior] + "\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _source(tmp_path: Path, text: str = DIVISIBILITY_SMOKE_THEOREM) -> Path:
    path = tmp_path / "Main.lean"
    path.write_text(text, encoding="utf-8")
    return path


def test_lean_unavailable(tmp_path: Path) -> None:
    result = run_lean_check(
        _source(tmp_path),
        lean_bin=str(tmp_path / "missing-lean"),
    )

    assert result["status"] == "unavailable"
    assert result["exit_code"] is None


def test_lean_success_on_divisibility_smoke(tmp_path: Path) -> None:
    result = run_lean_check(
        _source(tmp_path),
        lean_bin=str(_fake_lean(tmp_path, "success")),
    )

    assert result["status"] == "success"
    assert result["exit_code"] == 0
    assert result["proof_holes"] == []


def test_user_elan_bin_is_found_without_shell_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    elan_bin = home / ".elan" / "bin"
    elan_bin.mkdir(parents=True)
    fake = _fake_lean(elan_bin, "success")
    fake.rename(elan_bin / "lean")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    result = run_lean_check(_source(tmp_path))

    assert result["status"] == "success"
    assert result["command"][0] == str((elan_bin / "lean").resolve())
    assert result["tools"]["lean"]["available"] is True


def test_lake_runs_from_source_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source_dir = workspace / "Proofs"
    source_dir.mkdir(parents=True)
    (workspace / "lakefile.toml").write_text(
        'name = "test_workspace"\n',
        encoding="utf-8",
    )

    result = run_lean_check(
        _source(source_dir),
        lake_bin=str(_fake_lean(tmp_path, "success")),
        use_lake=True,
    )

    assert result["status"] == "success"
    assert result["cwd"] == str(workspace)


def test_lake_uses_persistent_mathlib_workspace_for_external_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = home / ".local" / "share" / "argus-skill" / "mathlib"
    workspace.mkdir(parents=True)
    (workspace / "lakefile.toml").write_text(
        'name = "test_workspace"\n',
        encoding="utf-8",
    )
    external = tmp_path / "external"
    external.mkdir()
    monkeypatch.setenv("HOME", str(home))

    result = run_lean_check(
        _source(external),
        lake_bin=str(_fake_lean(tmp_path, "success")),
        use_lake=True,
    )

    assert result["status"] == "success"
    assert result["cwd"] == str(workspace.resolve())


def test_packaged_divisibility_smoke_matches_checked_source() -> None:
    packaged = (
        Path(__file__).parents[2]
        / "argus_skill"
        / "verticals"
        / "math"
        / "divisibility_smoke.lean"
    )

    assert packaged.read_text(encoding="utf-8") == DIVISIBILITY_SMOKE_THEOREM


def test_packaged_erdos_straus_local_identity_is_bounded() -> None:
    packaged = (
        Path(__file__).parents[2]
        / "argus_skill"
        / "verticals"
        / "math"
        / "erdos_straus_even_local.lean"
    )
    source = packaged.read_text(encoding="utf-8")

    assert "erdos_straus_even_local_identity" in source
    assert "(4 : ℚ) / (2 * m)" in source
    assert find_proof_holes(source) == []


@pytest.mark.parametrize(
    ("behavior", "expected"),
    [("syntax", "syntax_error"), ("type", "type_error")],
)
def test_lean_compile_errors(
    tmp_path: Path,
    behavior: str,
    expected: str,
) -> None:
    result = run_lean_check(
        _source(tmp_path),
        lean_bin=str(_fake_lean(tmp_path, behavior)),
    )

    assert result["status"] == expected
    assert result["exit_code"] == 1


def test_lean_timeout(tmp_path: Path) -> None:
    result = run_lean_check(
        _source(tmp_path),
        lean_bin=str(_fake_lean(tmp_path, "timeout")),
        timeout_seconds=0.05,
    )

    assert result["status"] == "timeout"


def test_lean_compiler_proof_hole_warning_is_rejected(tmp_path: Path) -> None:
    result = run_lean_check(
        _source(tmp_path),
        lean_bin=str(_fake_lean(tmp_path, "warning")),
    )

    assert result["status"] == "proof_hole"


def test_lean_environment_axiom_audit_is_rejected(tmp_path: Path) -> None:
    result = run_lean_check(
        _source(tmp_path, "theorem bogus : False := forged\n"),
        lean_bin=str(_fake_lean(tmp_path, "meta-axiom")),
    )

    assert result["status"] == "proof_hole"
    assert result["audit_exit_code"] == 3
    assert result["proof_holes"] == [
        {
            "kind": "environment_axiom",
            "line": None,
            "declaration": "forged",
        }
    ]


@pytest.mark.parametrize("hole", ["sorry", "admit"])
def test_lean_rejects_proof_holes(tmp_path: Path, hole: str) -> None:
    result = run_lean_check(
        _source(tmp_path, f"theorem bad : True := by\n  {hole}\n"),
        lean_bin=str(_fake_lean(tmp_path, "success")),
    )

    assert result["status"] == "proof_hole"
    assert result["proof_holes"] == [{"kind": hole, "line": 2}]


@pytest.mark.parametrize("declaration", ["axiom forged : False", "constant forged : False"])
def test_lean_rejects_local_assumptions(
    tmp_path: Path,
    declaration: str,
) -> None:
    result = run_lean_check(
        _source(
            tmp_path,
            f"{declaration}\ntheorem bogus : False := forged\n",
        ),
        lean_bin=str(_fake_lean(tmp_path, "success")),
    )

    assert result["status"] == "proof_hole"
    assert result["proof_holes"] == [
        {"kind": declaration.split()[0], "line": 1}
    ]


def test_proof_hole_words_in_comments_and_strings_are_ignored() -> None:
    source = '-- sorry\n/- admit -/\ndef label : String := "sorry"\ntheorem ok : True := by trivial\n'

    assert find_proof_holes(source) == []


def test_string_comment_marker_does_not_hide_real_sorry() -> None:
    source = 'theorem bad : True := by\n  let marker := "--"\n  sorry\n'

    assert find_proof_holes(source) == [{"kind": "sorry", "line": 3}]


def test_nested_block_comments_are_ignored() -> None:
    source = "/- outer /- sorry -/ admit -/\ntheorem ok : True := by trivial\n"

    assert find_proof_holes(source) == []


def test_raw_strings_and_escaped_identifiers_are_ignored() -> None:
    source = (
        'def label : String := r#""sorry and admit""#\n'
        "def «sorry» : Nat := 0\n"
        "theorem ok : True := by trivial\n"
    )

    assert find_proof_holes(source) == []


def test_option_like_source_name_is_compiled_as_absolute_path(
    tmp_path: Path,
) -> None:
    source = tmp_path / "--version"
    source.write_text(DIVISIBILITY_SMOKE_THEOREM, encoding="utf-8")

    result = run_lean_check(
        source,
        lean_bin=str(_fake_lean(tmp_path, "success")),
    )

    assert result["status"] == "success"
    assert result["command"][-1] == str(source.resolve())


def test_invalid_utf8_returns_structured_failure(tmp_path: Path) -> None:
    source = tmp_path / "Main.lean"
    source.write_bytes(b"\xff")

    result = run_lean_check(
        source,
        lean_bin=str(_fake_lean(tmp_path, "success")),
    )

    assert result["status"] == "syntax_error"
    assert "cannot read source" in result["stderr"]


def test_cli_writes_structured_json(tmp_path: Path, capsys) -> None:
    output = tmp_path / "lean_check.json"
    rc = main(
        [
            str(_source(tmp_path)),
            "--lean-bin",
            str(_fake_lean(tmp_path, "success")),
            "--output",
            str(output),
        ]
    )

    assert rc == 0
    assert json.loads(output.read_text())["status"] == "success"
    assert json.loads(capsys.readouterr().out)["status"] == "success"
