from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.release_tools import typecheck_gate


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
