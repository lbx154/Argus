"""F1: a failing acceptance check's FULL output is persisted to disk and the
prompt-facing renderers carry only the absolute path (codex greps it on demand),
instead of inlining the whole (potentially MB-scale) log into BOTH the engineer
and reviewer prompts every round. A bounded head+tail window is inlined ONLY as a
fallback when there is no workdir to persist the log.
"""
from __future__ import annotations

from pathlib import Path

from argus_skill.core.models import CheckResult
from argus_skill.engineer.checks import (
    head_tail_window,
    run_checks,
    summarize_checks,
)
from argus_skill.engineer.runner import failed_check_diagnostics


# --------------------------------------------------------------------------- #
# head_tail_window primitive
# --------------------------------------------------------------------------- #
def test_head_tail_window_keeps_head_and_tail() -> None:
    text = "HEAD_TOKEN" + ("m" * 100_000) + "TAIL_TOKEN"
    out = head_tail_window(text, head=20, tail=20)
    assert "HEAD_TOKEN" in out
    assert "TAIL_TOKEN" in out
    assert "elided" in out
    assert len(out) < 200  # the giant middle is gone


def test_head_tail_window_short_text_unchanged() -> None:
    assert head_tail_window("small error", head=4000, tail=16000) == "small error"


# --------------------------------------------------------------------------- #
# run_checks: persist full log on failure, path-only into prompts
# --------------------------------------------------------------------------- #
def test_failing_check_writes_full_log_and_sets_path(tmp_path: Path) -> None:
    art = tmp_path / ".argus" / "checks" / "round-1"
    big = "BUILD_BANNER_START " + ("x" * 80_000) + " FATAL_TRACEBACK_END"
    [res] = run_checks(
        [f"printf %s {big!r}; exit 7"],
        timeout_seconds=30,
        cwd=str(tmp_path),
        artifacts_dir=art,
    )
    assert res.passed is False
    assert res.exit_code == 7
    assert res.output_path  # an absolute path was set
    logfile = Path(res.output_path)
    assert logfile.exists()
    full = logfile.read_text()
    # The FULL log (both ends + the giant middle) is on disk.
    assert "BUILD_BANNER_START" in full and "FATAL_TRACEBACK_END" in full
    assert len(full) > 80_000


def test_passing_check_is_not_persisted(tmp_path: Path) -> None:
    art = tmp_path / ".argus" / "checks" / "round-1"
    [res] = run_checks(
        ["echo ok"], timeout_seconds=30, cwd=str(tmp_path), artifacts_dir=art
    )
    assert res.passed is True
    assert res.output_path == ""


def test_no_artifacts_dir_is_backcompat(tmp_path: Path) -> None:
    # No artifacts_dir → nothing persisted, output_tail still populated, no crash.
    [res] = run_checks(
        ["echo boom; exit 1"], timeout_seconds=30, cwd=str(tmp_path)
    )
    assert res.passed is False
    assert res.output_path == ""
    assert "boom" in res.output_tail


# --------------------------------------------------------------------------- #
# Prompt renderers: path-only when persisted, never the blob
# --------------------------------------------------------------------------- #
_HUGE = "SECRET_BLOB_" + ("z" * 200_000)


def test_summarize_checks_path_only_when_persisted() -> None:
    res = CheckResult(
        command="nvcc build",
        exit_code=1,
        passed=False,
        output_tail=_HUGE,
        output_path="/work/.argus/checks/round-3/00-nvcc-build.log",
    )
    out = summarize_checks([res])
    assert "/work/.argus/checks/round-3/00-nvcc-build.log" in out
    assert "grep" in out
    assert "SECRET_BLOB_" not in out  # the blob never reaches the prompt
    assert len(out) < 500


def test_failed_check_diagnostics_path_only_when_persisted() -> None:
    res = CheckResult(
        command="pytest",
        exit_code=1,
        passed=False,
        output_tail=_HUGE,
        output_path="/work/.argus/checks/round-3/01-pytest.log",
    )
    out = failed_check_diagnostics([res])
    assert "/work/.argus/checks/round-3/01-pytest.log" in out
    assert "grep" in out
    assert "SECRET_BLOB_" not in out
    assert "pytest" in out and "exit=1" in out


def test_diagnostics_inline_fallback_bounded_when_no_path() -> None:
    # No output_path → inline fallback, bounded, but keeps the real error tail.
    long_tail = ("noise " * 10_000) + " the_real_error_token"
    res = CheckResult(
        command="bash ./eval.sh", exit_code=1, passed=False, output_tail=long_tail
    )
    out = failed_check_diagnostics([res], max_chars=1200)
    assert "the_real_error_token" in out
    assert len(out) < 4000  # bounded, not the whole 60KB log


def test_summarize_inline_fallback_is_windowed_when_no_path() -> None:
    res = CheckResult(
        command="bash ./eval.sh",
        exit_code=1,
        passed=False,
        output_tail="HEAD_X" + ("q" * 200_000) + "TAIL_Y",
    )
    out = summarize_checks([res])
    assert "HEAD_X" in out and "TAIL_Y" in out
    # ~20KB head+tail window, not the 200KB blob.
    assert len(out) < 25_000
    assert "elided" in out
