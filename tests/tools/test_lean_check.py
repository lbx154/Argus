from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.tools.lean_check import (
    CANONICAL_LEAN_SOURCE,
    COMPILE_LOG,
    DIVISIBILITY_SMOKE_THEOREM,
    LEAN_CHECK_RESULT,
    STATEMENT_FIDELITY,
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
        "audit-timeout": (
            "import sys, time; "
            "time.sleep(5) if '--run' in sys.argv else None; "
            "raise SystemExit(0)"
        ),
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


def _fake_lake_with_versions(tmp_path: Path) -> Path:
    path = tmp_path / "lake-versioned"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if args == ['--version']:\n"
        "    print('Lake workspace version')\n"
        "elif args == ['env', 'lean', '--version']:\n"
        "    print('Lean workspace version')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
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


def test_lake_versions_are_probed_in_the_compilation_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "lakefile.toml").write_text(
        'name = "test_workspace"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    result = run_lean_check(
        _source(workspace),
        lake_bin=str(_fake_lake_with_versions(tmp_path)),
        use_lake=True,
    )

    assert result["status"] == "success"
    assert result["tools"]["lake"]["version"] == "Lake workspace version"
    assert result["tools"]["lean"]["version"] == "Lean workspace version"
    assert result["tools"]["lean"]["path"].endswith("lake-versioned env lean")


def test_lake_proof_hole_uses_workspace_version_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "lakefile.toml").write_text(
        'name = "test_workspace"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    result = run_lean_check(
        _source(workspace, "theorem bad : True := by\n  sorry\n"),
        lake_bin=str(_fake_lake_with_versions(tmp_path)),
        use_lake=True,
    )

    assert result["status"] == "proof_hole"
    assert result["tools"]["lake"]["version"] == "Lake workspace version"
    assert result["tools"]["lean"]["version"] == "Lean workspace version"


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


def test_divisibility_fixture_matches_checked_source() -> None:
    packaged = (
        Path(__file__).parents[2]
        / "tests"
        / "fixtures"
        / "lean"
        / "divisibility_smoke.lean"
    )

    assert packaged.read_text(encoding="utf-8") == DIVISIBILITY_SMOKE_THEOREM


def test_erdos_straus_fixture_is_bounded() -> None:
    packaged = (
        Path(__file__).parents[2]
        / "tests"
        / "fixtures"
        / "lean"
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
    assert result["exit_code"] is not None


def test_lean_audit_timeout_records_killed_exit(tmp_path: Path) -> None:
    result = run_lean_check(
        _source(tmp_path),
        lean_bin=str(_fake_lean(tmp_path, "audit-timeout")),
        # Leave enough time for the fake Python compiler to start and exit;
        # only its ``--run`` audit path sleeps for five seconds. A 50 ms shared
        # timeout flakes under host load by killing the compile phase instead.
        timeout_seconds=0.5,
    )

    assert result["status"] == "timeout"
    assert result["exit_code"] == 0
    assert result["audit_exit_code"] is not None


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


def test_cli_materializes_four_canonical_math_artifacts(
    tmp_path: Path,
    capsys,
) -> None:
    descriptive_source = tmp_path / "DivisibilityTransitive.lean"
    descriptive_source.write_text(
        "import Mathlib\n"
        "theorem int_dvd_transitive (a b c : ℤ) "
        "(hab : a ∣ b) (hbc : b ∣ c) : a ∣ c := hab.trans hbc\n",
        encoding="utf-8",
    )
    fidelity_source = tmp_path / "fidelity-input.md"
    fidelity_source.write_text(
        "# Statement Fidelity\n\n"
        "- Domain: `a b c : ℤ`.\n"
        "- Hypotheses: `a ∣ b` and `b ∣ c`.\n"
        "- Conclusion: `a ∣ c`.\n"
        "- Added assumptions: none.\n",
        encoding="utf-8",
    )
    artifact_dir = tmp_path / "artifacts"

    rc = main(
        [
            str(descriptive_source),
            "--lean-bin",
            str(_fake_lean(tmp_path, "success")),
            "--artifact-dir",
            str(artifact_dir),
            "--statement-fidelity",
            str(fidelity_source),
        ]
    )

    assert rc == 0
    assert descriptive_source.exists()
    assert (artifact_dir / CANONICAL_LEAN_SOURCE).read_text() == (
        descriptive_source.read_text()
    )
    assert (artifact_dir / STATEMENT_FIDELITY).read_text() == (
        fidelity_source.read_text()
    )
    result = json.loads((artifact_dir / LEAN_CHECK_RESULT).read_text())
    assert result["status"] == "success"
    assert result["source"] == str(
        (artifact_dir / CANONICAL_LEAN_SOURCE).resolve()
    )
    compile_log = (artifact_dir / COMPILE_LOG).read_text()
    assert str((artifact_dir / CANONICAL_LEAN_SOURCE).resolve()) in compile_log
    assert "exit_code: 0" in compile_log
    assert "audit_exit_code: 0" in compile_log
    assert json.loads(capsys.readouterr().out)["status"] == "success"


def test_artifact_mode_rejects_symlink_destinations(tmp_path: Path) -> None:
    source = tmp_path / "Descriptive.lean"
    source.write_text(DIVISIBILITY_SMOKE_THEOREM, encoding="utf-8")
    fidelity = tmp_path / "fidelity.md"
    fidelity.write_text("# Fidelity\n\nNo added assumptions.\n", encoding="utf-8")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    outside = tmp_path / "outside.lean"
    outside.write_text("do not overwrite\n", encoding="utf-8")
    (artifact_dir / CANONICAL_LEAN_SOURCE).symlink_to(outside)

    with pytest.raises(SystemExit) as exc:
        main(
            [
                str(source),
                "--lean-bin",
                str(_fake_lean(tmp_path, "success")),
                "--artifact-dir",
                str(artifact_dir),
                "--statement-fidelity",
                str(fidelity),
            ]
        )

    assert exc.value.code == 2
    assert outside.read_text() == "do not overwrite\n"


def test_artifact_mode_rejects_output_collision(tmp_path: Path) -> None:
    source = tmp_path / "Descriptive.lean"
    source.write_text(DIVISIBILITY_SMOKE_THEOREM, encoding="utf-8")
    fidelity = tmp_path / "fidelity.md"
    fidelity.write_text("# Fidelity\n\nNo added assumptions.\n", encoding="utf-8")
    artifact_dir = tmp_path / "artifacts"

    with pytest.raises(SystemExit) as exc:
        main(
            [
                str(source),
                "--lean-bin",
                str(_fake_lean(tmp_path, "success")),
                "--artifact-dir",
                str(artifact_dir),
                "--statement-fidelity",
                str(fidelity),
                "--output",
                str(artifact_dir / COMPILE_LOG),
            ]
        )

    assert exc.value.code == 2
    assert not (artifact_dir / COMPILE_LOG).exists()


@pytest.mark.parametrize("target", ["source", "fidelity"])
def test_artifact_mode_output_cannot_overwrite_inputs(
    tmp_path: Path,
    target: str,
) -> None:
    source = tmp_path / "Descriptive.lean"
    source.write_text(DIVISIBILITY_SMOKE_THEOREM, encoding="utf-8")
    fidelity = tmp_path / "fidelity.md"
    fidelity.write_text("# Fidelity\n\nNo added assumptions.\n", encoding="utf-8")
    artifact_dir = tmp_path / "artifacts"
    output = source if target == "source" else fidelity

    with pytest.raises(SystemExit) as exc:
        main(
            [
                str(source),
                "--lean-bin",
                str(_fake_lean(tmp_path, "success")),
                "--artifact-dir",
                str(artifact_dir),
                "--statement-fidelity",
                str(fidelity),
                "--output",
                str(output),
            ]
        )

    assert exc.value.code == 2
    assert source.read_text() == DIVISIBILITY_SMOKE_THEOREM
    assert fidelity.read_text() == "# Fidelity\n\nNo added assumptions.\n"


def test_artifact_mode_rejects_source_aliasing_compile_log(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    source = artifact_dir / COMPILE_LOG
    source.write_text(DIVISIBILITY_SMOKE_THEOREM, encoding="utf-8")
    fidelity = tmp_path / "fidelity.md"
    fidelity.write_text("# Fidelity\n\nNo added assumptions.\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        main(
            [
                str(source),
                "--lean-bin",
                str(_fake_lean(tmp_path, "success")),
                "--artifact-dir",
                str(artifact_dir),
                "--statement-fidelity",
                str(fidelity),
            ]
        )

    assert exc.value.code == 2
    assert source.read_text() == DIVISIBILITY_SMOKE_THEOREM


def test_artifact_mode_rejects_fidelity_aliasing_main(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    source = tmp_path / "Descriptive.lean"
    source.write_text(DIVISIBILITY_SMOKE_THEOREM, encoding="utf-8")
    fidelity = artifact_dir / CANONICAL_LEAN_SOURCE
    fidelity.write_text("# Not a valid source replacement\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        main(
            [
                str(source),
                "--lean-bin",
                str(_fake_lean(tmp_path, "success")),
                "--artifact-dir",
                str(artifact_dir),
                "--statement-fidelity",
                str(fidelity),
            ]
        )

    assert exc.value.code == 2
    assert fidelity.read_text() == "# Not a valid source replacement\n"
