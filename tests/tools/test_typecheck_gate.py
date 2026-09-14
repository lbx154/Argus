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
