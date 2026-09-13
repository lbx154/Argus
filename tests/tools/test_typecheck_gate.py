from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus_skill.release_tools import typecheck_gate


def test_moving_diagnostics_does_not_hide_a_different_new_error():
    old = typecheck_gate.diagnostic_counts(
        'argus_skill/a.py:10: error: Missing attribute "x" [attr-defined]\n'
        'argus_skill/b.py:20: error: Wrong argument [arg-type]\n'
    )
    new = typecheck_gate.diagnostic_counts(
        'argus_skill/a.py:100:5: error: Missing attribute "x" [attr-defined]\n'
        'argus_skill/c.py:30: error: Wrong argument [arg-type]\n'
        'argus_skill/a.py:101: note: See the declaration\n'
    )
    assert old.total() == new.total()
    assert new - old == {'argus_skill/c.py: Wrong argument [arg-type]': 1}


def test_a_second_identical_error_is_additional_debt():
    first = 'argus_skill/a.py:10: error: Wrong argument [arg-type]\n'
    second = 'argus_skill/a.py:11: error: Wrong argument [arg-type]\n'
    assert (typecheck_gate.diagnostic_counts(first + second)
            - typecheck_gate.diagnostic_counts(first)).total() == 1


def test_moving_a_duplicate_definition_does_not_create_a_false_regression():
    old = 'argus_skill/a.py:10: error: Name "step" already defined on line 2  [no-redef]'
    new = 'argus_skill/a.py:100: error: Name "step" already defined on line 20  [no-redef]'
    assert typecheck_gate.diagnostic_counts(old) == typecheck_gate.diagnostic_counts(new)


@pytest.mark.parametrize("returncode,output", [(2, "Internal error"), (1, "No module named mypy")])
def test_failed_typechecker_cannot_pass_the_gate(tmp_path, monkeypatch, returncode, output):
    monkeypatch.setattr(typecheck_gate.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=returncode, stdout="", stderr=output,
    ))
    with pytest.raises(RuntimeError, match="mypy could not complete"):
        typecheck_gate._run_mypy(tmp_path)
