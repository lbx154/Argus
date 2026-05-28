"""PPTX run report generation.

Builds a structured JSON payload from run data and invokes a Node.js
script (``pptx/generate_run_report.js``) to produce a styled PPTX
presentation.  Designed to be called from ``LoopEngine._complete``
after the Markdown report.  Failures are always non-fatal so that the
loop never blocks on a missing ``node`` binary or a broken template.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import RoundSummary

logger = logging.getLogger(__name__)

# Resolve the JS script path relative to *this* file's package root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_DIR = _PROJECT_ROOT / "skills" / "pptx-run-report"
_JS_SCRIPT = _SCRIPT_DIR / "generate_run_report.js"


def build_report_data(
    *,
    objective: str,
    rounds: list[RoundSummary],
    session_id: str | None,
    success: bool,
    stop_reason: str,
    operator_messages: list[str] | None = None,
    plan_mode: str = "off",
) -> dict[str, Any]:
    """Extract structured data from run results into a dict for the JS template."""
    total_rounds = len(rounds)
    last_round = rounds[-1] if rounds else None

    # Checks from last round
    final_checks: list[dict[str, Any]] = []
    checks_passed = 0
    checks_failed = 0
    if last_round:
        for check in last_round.checks:
            final_checks.append({
                "command": check.command,
                "exit_code": check.exit_code,
                "passed": check.passed,
            })
            if check.passed:
                checks_passed += 1
            else:
                checks_failed += 1

    # Reviewer info from last round
    reviewer_verdict = last_round.review.status if last_round else None
    reviewer_reason = last_round.review.reason if last_round else None
    reviewer_next_action = last_round.review.next_action if last_round else None

    # Planner info from last round
    planner_follow_up_required = None
    planner_next_explore = None
    planner_main_instruction = None
    if last_round and last_round.plan is not None:
        planner_follow_up_required = last_round.plan.follow_up_required
        planner_next_explore = last_round.plan.next_explore
        planner_main_instruction = last_round.plan.main_instruction

    # Round summaries for timeline
    round_data = []
    for r in rounds:
        round_data.append({
            "round_index": r.round_index,
            "review_status": r.review.status,
            "review_confidence": r.review.confidence,
            "checks_passed": all(c.passed for c in r.checks) if r.checks else True,
            "main_turn_failed": r.main_turn_failed,
        })

    objective_short = objective[:80] + "..." if len(objective) > 80 else objective

    return {
        "objective": objective,
        "objective_short": objective_short,
        "session_id": session_id,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "success": success,
        "stop_reason": stop_reason,
        "total_rounds": total_rounds,
        "checks_passed": checks_passed,
        "checks_failed": checks_failed,
        "checks_total": checks_passed + checks_failed,
        "reviewer_verdict": reviewer_verdict,
        "reviewer_reason": reviewer_reason,
        "reviewer_next_action": reviewer_next_action,
        "planner_follow_up_required": planner_follow_up_required,
        "planner_next_explore": planner_next_explore,
        "planner_main_instruction": planner_main_instruction,
        "plan_mode": plan_mode,
        "final_checks": final_checks,
        "rounds": round_data,
        "operator_messages": operator_messages or [],
    }


def generate_pptx_report(
    *,
    objective: str,
    rounds: list[RoundSummary],
    session_id: str | None,
    success: bool,
    stop_reason: str,
    output_path: str,
    operator_messages: list[str] | None = None,
    plan_mode: str = "off",
) -> str | None:
    """Generate a PPTX report.  Returns the output path on success, or
    ``None`` if generation failed (with a warning logged)."""
    try:
        data = build_report_data(
            objective=objective,
            rounds=rounds,
            session_id=session_id,
            success=success,
            stop_reason=stop_reason,
            operator_messages=operator_messages,
            plan_mode=plan_mode,
        )
        return _run_js_generator(data=data, output_path=output_path)
    except Exception:
        logger.warning("PPTX report generation failed", exc_info=True)
        return None


def _run_js_generator(*, data: dict[str, Any], output_path: str) -> str | None:
    """Write JSON to temp file, invoke node script, return output path."""
    if not _JS_SCRIPT.exists():
        logger.warning("PPTX JS script not found at %s", _JS_SCRIPT)
        return None

    # Resolve to absolute so the node subprocess (which runs with cwd=pptx/)
    # writes to the correct location.
    output_path = str(Path(output_path).resolve())

    # Ensure output dir exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=True)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            ["node", str(_JS_SCRIPT), tmp_path, output_path],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(_PROJECT_ROOT),
        )
        if result.returncode != 0:
            logger.warning(
                "PPTX JS script exited with code %d: %s",
                result.returncode,
                result.stderr[:500],
            )
            return None
        if Path(output_path).exists():
            return output_path
        logger.warning("PPTX output file not created at %s", output_path)
        return None
    except FileNotFoundError:
        logger.warning("node binary not found; cannot generate PPTX report")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("PPTX generation timed out after 60s")
        return None
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
