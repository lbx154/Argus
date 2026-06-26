"""Tests for D1: runner gate-veto path.

The integration runs ``argus_skill.tools.stage_check`` as a per-round check
command. When an automated gate (F3 / F4) fails, stage_check exits non-zero,
which trips ``_coerce_review_for_failed_checks``, which forces the
ReviewDecision to ``status="continue"`` even if the reviewer LLM said
``"done"``. This file verifies that path end-to-end and that the
gate-specific handoff is rendered into the reviewer's next_action.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from argus_skill.core.models import CheckResult, ReviewDecision
from argus_skill.engineer.runner import (
    _coerce_review_for_failed_checks,
    _extract_gate_failures,
    _fallback_failed_check_handoff,
)

# ---------------------------------------------------------------------------
# _extract_gate_failures: parse stage_check output_tail
# ---------------------------------------------------------------------------


_STAGE_CHECK_FAIL_OUTPUT = """\
📋 Stage: review

  ❌ Pipeline state present
  ❌ Some other shell check

🛡  Automated gates for stage 'review':
  ❌ evidence_chain — 1 chain issue(s) across 9 claim(s)
     1 chain integrity issue(s); draft cannot advance to submission until fixed:
       [bundle_missing_build_info] x1
         - foo (...): bundle has no BUILD_INFO.md
  ❌ anti_mediocrity — 1 gate failure(s)
     1 anti-mediocrity gate failure(s):
       [benchmark_diversity_insufficient] only 0 distinct ...

📋 REVIEWER CHECKLIST for stage 'review'
   Load skill: ...
"""

_STAGE_CHECK_PASS_OUTPUT = """\
📋 Stage: review

  ✅ Pipeline state present

🛡  Automated gates for stage 'review':
  ✅ evidence_chain — all claims resolve cleanly
  ✅ anti_mediocrity — all gates pass
"""


def _check(passed: bool, output_tail: str = "") -> CheckResult:
    return CheckResult(
        command="{argus_python} -m argus_skill.tools.stage_check --project-root .",
        exit_code=0 if passed else 1,
        passed=passed,
        output_tail=output_tail,
    )


def test_extract_gate_failures_picks_both_failed_gates() -> None:
    failures = _extract_gate_failures(_check(False, _STAGE_CHECK_FAIL_OUTPUT))
    assert any("gate:evidence_chain" in f for f in failures)
    assert any("gate:anti_mediocrity" in f for f in failures)
    assert len(failures) == 2


def test_extract_gate_failures_returns_empty_for_pass_output() -> None:
    failures = _extract_gate_failures(_check(True, _STAGE_CHECK_PASS_OUTPUT))
    assert failures == []


def test_extract_gate_failures_returns_empty_without_marker() -> None:
    failures = _extract_gate_failures(_check(False, "some unrelated output"))
    assert failures == []


# ---------------------------------------------------------------------------
# _fallback_failed_check_handoff: gate-aware text
# ---------------------------------------------------------------------------


def test_handoff_uses_gate_specific_text_when_gates_failed() -> None:
    failed = _check(False, _STAGE_CHECK_FAIL_OUTPUT)
    text = _fallback_failed_check_handoff([failed])
    assert "research-factory gates vetoed" in text
    assert "gate:evidence_chain" in text
    assert "gate:anti_mediocrity" in text
    assert "stage_check --project-root" in text
    # The old generic "rerun the exact failed command" is not used.
    assert "rerun the exact failed command" not in text


def test_handoff_falls_back_to_generic_text_for_shell_only_failures() -> None:
    failed = _check(False, "pytest exit 1")
    text = _fallback_failed_check_handoff([failed])
    assert "acceptance checks still fail" in text
    assert "research-factory gates" not in text


# ---------------------------------------------------------------------------
# End-to-end veto: reviewer says "done" but gate failed → forced "continue"
# ---------------------------------------------------------------------------


def test_reviewer_done_is_vetoed_when_gate_failed() -> None:
    failed = _check(False, _STAGE_CHECK_FAIL_OUTPUT)
    reviewer_verdict = ReviewDecision(
        status="done",
        reason="engineer reported success",
        next_action="No further action.",
        round_summary_markdown="# Review Summary\n\n- all good\n",
        completion_summary_markdown="# Completion\n\n- done\n",
    )

    coerced = _coerce_review_for_failed_checks(reviewer_verdict, [failed])

    assert coerced.status == "continue"
    assert "Acceptance checks failed" in coerced.reason
    # The next_action must surface the specific gate failures, not the
    # reviewer's "No further action."
    assert "gate:evidence_chain" in coerced.next_action
    assert "gate:anti_mediocrity" in coerced.next_action


def test_reviewer_continue_keeps_status_but_replaces_blank_next_action() -> None:
    failed = _check(False, _STAGE_CHECK_FAIL_OUTPUT)
    reviewer_verdict = ReviewDecision(
        status="continue",
        reason="checks failed, continuing",
        next_action="",  # reviewer left it empty
        round_summary_markdown="",
        completion_summary_markdown="",
    )

    coerced = _coerce_review_for_failed_checks(reviewer_verdict, [failed])

    assert coerced.status == "continue"
    # Reason kept; next_action filled with gate-aware handoff.
    assert "gate:evidence_chain" in coerced.next_action


def test_no_coercion_when_all_checks_pass() -> None:
    passed = _check(True, _STAGE_CHECK_PASS_OUTPUT)
    reviewer_verdict = ReviewDecision(
        status="done",
        reason="all clear",
        next_action="No further action.",
        round_summary_markdown="",
        completion_summary_markdown="",
    )

    coerced = _coerce_review_for_failed_checks(reviewer_verdict, [passed])

    # Untouched.
    assert coerced.status == "done"
    assert coerced.reason == "all clear"


# ---------------------------------------------------------------------------
# Subprocess: stage_check → exit code reflects gate failure
# ---------------------------------------------------------------------------


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _write_claims_tsv(root: Path, rows: list[dict[str, str]]) -> None:
    cols = [
        "claim_id", "status", "claim",
        "evidence_1", "evidence_2", "evidence_3", "notes",
    ]
    lines = ["\t".join(cols)]
    for row in rows:
        lines.append("\t".join(row.get(c, "") for c in cols))
    _write(root / "paper" / "claims_to_evidence.tsv", "\n".join(lines) + "\n")


def test_subprocess_stage_check_exits_nonzero_on_gate_failure(tmp_path: Path) -> None:
    _write(
        tmp_path / "research" / "PIPELINE_STATE.json",
        json.dumps({"current_stage": "draft"}),
    )
    _write_claims_tsv(
        tmp_path,
        [
            {
                "claim_id": "broken",
                "status": "current_evidence",
                "claim": "x",
                "evidence_1": "benchmarks/evidence/missing/summary.tsv",
            }
        ],
    )

    proc = subprocess.run(
        [
            sys.executable, "-m", "argus_skill.tools.stage_check",
            "--project-root", str(tmp_path),
            "--stage", "draft",
        ],
        capture_output=True,
        text=True,
    )

    # stage_check returns non-zero so run_checks marks the CheckResult as
    # failed; that triggers _coerce_review_for_failed_checks in the runner.
    assert proc.returncode != 0
    assert "❌ evidence_chain" in proc.stdout
    assert "evidence_path_missing" in proc.stdout

    # Simulate what runner.py sees: build a CheckResult from the subprocess
    # output and run the coercion against a "done" reviewer verdict.
    check = CheckResult(
        command="stage_check",
        exit_code=proc.returncode,
        passed=False,
        output_tail=proc.stdout,
    )
    reviewer_verdict = ReviewDecision(
        status="done",
        reason="engineer claims success",
        next_action="No further action.",
        round_summary_markdown="",
        completion_summary_markdown="",
    )
    coerced = _coerce_review_for_failed_checks(reviewer_verdict, [check])

    assert coerced.status == "continue"
    assert "gate:evidence_chain" in coerced.next_action
