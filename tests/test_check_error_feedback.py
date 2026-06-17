"""The engineer's structured error-feedback channel.

Regression guard for the systemic fix: in a complex iterative environment
(optimizing a GPU kernel, fixing a failing build) the engineer must SEE the
literal output of a failing acceptance check — the compile error, traceback,
or numeric mismatch — not just the reviewer's paraphrase. Withholding it forces
blind guess-and-revert loops. ``failed_check_diagnostics`` surfaces that raw
output to the next engineer turn.
"""
from __future__ import annotations

from argus_skill.core.models import CheckResult
from argus_skill.engineer.runner import failed_check_diagnostics


def _check(passed: bool, output_tail: str = "", command: str = "bash ./eval_solution.sh") -> CheckResult:
    return CheckResult(
        command=command,
        exit_code=0 if passed else 1,
        passed=passed,
        output_tail=output_tail,
    )


def test_empty_when_nothing_failed() -> None:
    assert failed_check_diagnostics([]) == ""
    assert failed_check_diagnostics([_check(True, "RESULT mean_SOL=0.7")]) == ""


def test_surfaces_the_real_error_output_verbatim() -> None:
    err = "40_LayerNorm  N  INCORRECT\n      └─ ERROR: numerical mismatch (max|d|=1.733 > atol=0.01) max_abs_err=1.7332"
    text = failed_check_diagnostics([_check(False, err)])
    # The literal error the engineer needs to fix must be present.
    assert "numerical mismatch (max|d|=1.733 > atol=0.01)" in text
    assert "max_abs_err=1.7332" in text
    # And the command + exit code so it knows what failed.
    assert "bash ./eval_solution.sh" in text
    assert "exit=1" in text


def test_includes_every_failing_check_skips_passing() -> None:
    checks = [
        _check(True, "ok", command="ruff"),
        _check(False, "RuntimeError: CUDA error: an illegal memory access", command="pytest"),
        _check(False, "error: identifier \"blockReduce\" is undefined", command="nvcc build"),
    ]
    text = failed_check_diagnostics(checks)
    assert "illegal memory access" in text
    assert "blockReduce" in text
    assert "pytest" in text and "nvcc build" in text
    assert "ruff" not in text  # passing check is not echoed


def test_bounded_but_keeps_the_error_tail() -> None:
    # The actual error is usually at the END of a long log; the budget must keep it.
    long_tail = ("noise line\n" * 5000) + "FATAL: the_real_error_token here"
    text = failed_check_diagnostics([_check(False, long_tail)], max_chars=1200)
    assert "the_real_error_token" in text
    assert len(text) < 4000  # bounded, does not dump the whole 50KB log
