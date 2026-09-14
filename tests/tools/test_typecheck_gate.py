from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.release_tools import typecheck_gate


def test_moving_diagnostics_does_not_hide_a_different_new_error():
    old = typecheck_gate.diagnostic_counts(
        'argus/a.py:10: error: Missing attribute "x" [attr-defined]\n'
        'argus/b.py:20: error: Wrong argument [arg-type]\n'
    )
    new = typecheck_gate.diagnostic_counts(
        'argus/a.py:100:5: error: Missing attribute "x" [attr-defined]\n'
        'argus/c.py:30: error: Wrong argument [arg-type]\n'
        'argus/a.py:101: note: See the declaration\n'
    )
    assert old.total() == new.total()
    assert new - old == {'argus/c.py: Wrong argument [arg-type]': 1}


def test_a_second_identical_error_is_additional_debt():
    first = 'argus/a.py:10: error: Wrong argument [arg-type]\n'
    second = 'argus/a.py:11: error: Wrong argument [arg-type]\n'
    assert (typecheck_gate.diagnostic_counts(first + second)
            - typecheck_gate.diagnostic_counts(first)).total() == 1


def test_moving_a_duplicate_definition_does_not_create_a_false_regression():
    old = 'argus/a.py:10: error: Name "step" already defined on line 2  [no-redef]'
    new = 'argus/a.py:100: error: Name "step" already defined on line 20  [no-redef]'
    assert typecheck_gate.diagnostic_counts(old) == typecheck_gate.diagnostic_counts(new)


def test_source_comparison_normalizes_paths_and_retains_only_first_party_diagnostics():
    output = (
        'argus\\a.py:10: error: Invalid value [arg-type]\n'
        '.venv/Lib/site-packages/example.py:11: error: Dependency debt [arg-type]\n'
    )
    assert typecheck_gate.diagnostic_counts(output, source_only=True) == {
        'argus/a.py: Invalid value [arg-type]': 1,
    }
    assert typecheck_gate.diagnostic_counts(output).total() == 2


def test_diagnostics_from_a_base_that_predates_the_package_rename_are_comparable():
    """A base revision still laid out as ``argus_skill/`` keys its debt by the current path."""
    before = typecheck_gate.diagnostic_counts(
        'argus_skill/a.py:10: error: Wrong argument [arg-type]\n'
        'argus_skill\\b.py:11: error: Missing attribute "x" [attr-defined]\n',
        source_only=True,
    )
    after = typecheck_gate.diagnostic_counts(
        'argus/a.py:12: error: Wrong argument [arg-type]\n'
        'argus/b.py:13: error: Missing attribute "x" [attr-defined]\n',
        source_only=True,
    )
    assert before == after
    assert set(after) == {'argus/a.py: Wrong argument [arg-type]', 'argus/b.py: Missing attribute "x" [attr-defined]'}
    # The alias package's own diagnostics (there are none: it is not type-checked)
    # would not masquerade as another file's.
    assert typecheck_gate.diagnostic_counts(
        'argus_skillful/a.py:1: error: Other [misc]\n', source_only=True,
    ) == {}


def test_base_archive_falls_back_to_the_pre_rename_package_directory(tmp_path, monkeypatch):
    attempts = []

    def run(command, **kwargs):
        attempts.append(list(command))
        if "argus_skill" in command:
            return SimpleNamespace(returncode=0, stdout=b"tarball", stderr=b"")
        return SimpleNamespace(returncode=128, stdout=b"", stderr=b"fatal: pathspec 'argus' did not match any files")

    monkeypatch.setattr(typecheck_gate.subprocess, "run", run)

    assert typecheck_gate._archive_base("abc123", tmp_path) == b"tarball"
    assert [command[-2] for command in attempts] == ["argus", "argus_skill"]
    assert all(command[:4] == ["git", "archive", "abc123", "--"] for command in attempts)


def test_base_archive_prefers_the_current_package_directory(tmp_path, monkeypatch):
    attempts = []
    monkeypatch.setattr(typecheck_gate.subprocess, "run", lambda command, **kwargs: (
        attempts.append(list(command)) or SimpleNamespace(returncode=0, stdout=b"tarball", stderr=b"")
    ))

    assert typecheck_gate._archive_base("abc123", tmp_path) == b"tarball"
    assert [command[-2] for command in attempts] == ["argus"]


def test_base_archive_failure_is_reported_as_a_git_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(typecheck_gate.subprocess, "run", lambda command, **kwargs: SimpleNamespace(
        returncode=128, stdout=b"", stderr=b"fatal: bad revision",
    ))
    with pytest.raises(typecheck_gate.subprocess.CalledProcessError):
        typecheck_gate._archive_base("nope", tmp_path)


def test_editable_install_and_baseline_are_checked_without_silencing(tmp_path, monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(typecheck_gate.subprocess, "run", run)
    typecheck_gate._run_mypy(tmp_path)
    assert "--no-silence-site-packages" in commands[0]
    assert "--no-incremental" in commands[0]


@pytest.mark.parametrize("returncode,output", [(2, "Internal error"), (1, "No module named mypy")])
def test_failed_typechecker_cannot_pass_the_gate(tmp_path, monkeypatch, returncode, output):
    monkeypatch.setattr(typecheck_gate.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=returncode, stdout="", stderr=output,
    ))
    with pytest.raises(RuntimeError, match="mypy could not complete"):
        typecheck_gate._run_mypy(tmp_path)
