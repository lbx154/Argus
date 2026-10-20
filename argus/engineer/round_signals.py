"""Small event, secret-guard, and durable-wait helpers for Engineer rounds."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..core import process_stop
from ..core.event_catalog import EventType
from ..core.models import ReviewDecision
from ..core.secret_guard import (
    SecretScrubReport,
    known_secret_values,
    redact_secrets_record,
    scrub_recent_text_artifacts,
)
from .external_work import wait_for_external_work_cadence


def _review_event_payload(
    review: ReviewDecision,
    *,
    round_index: int,
    round_max: int,
    text: str,
    review_skipped: bool = False,
) -> dict[str, object]:
    return redact_secrets_record(
        review.to_event_payload(
            round_index=round_index,
            round_max=round_max,
            text=text,
            review_skipped=review_skipped,
        ),
        known_values=known_secret_values(),
    )




def _apply_round_secret_guard(
    *,
    workdir: Path,
    modified_since: float,
    round_index: int,
    round_max: int,
    on_event: Callable[[dict], None] | None,
) -> tuple[SecretScrubReport, str]:
    report = scrub_recent_text_artifacts(
        workdir,
        modified_since=modified_since,
        known_values=known_secret_values(),
    )
    if not report.changed and not report.errors and not report.truncated:
        return report, ""
    if on_event:
        on_event({
            "type": EventType.ROUND_SECRET_REDACTED,
            "round_index": round_index,
            "round_max": round_max,
            "redacted_paths": list(report.redacted_paths),
            "replacement_count": report.replacement_count,
            "scanned_files": report.scanned_files,
            "scan_errors": list(report.errors),
            "truncated": report.truncated,
            "skipped_paths": [
                {"path": path, "bytes": size}
                for path, size in report.skipped_paths
            ],
            "operator_alert": bool(report.errors or report.truncated),
        })
    lines = ["SECURITY GUARD (authoritative artifact hygiene):"]
    if report.changed:
        lines.extend((
            f"- Redacted {report.replacement_count} credential occurrence(s) "
            f"from {len(report.redacted_paths)} changed file(s) before review.",
            "- Files: " + ", ".join(report.redacted_paths),
            "- Revalidate dependent hashes or provenance before completion.",
        ))
    if report.skipped_paths:
        lines.append(
            "- Coverage incomplete: NOT scanned for secrets: "
            + ", ".join(
                # Size-0 entries are summaries ("+N more files") or files
                # whose stat failed; a "0.0 MiB" suffix would misread both.
                f"{path} ({size / (1024 * 1024):.1f} MiB)" if size else path
                for path, size in report.skipped_paths
            )
            + ". Inspect these files manually or split them before sharing."
        )
    if report.errors:
        lines.append("- Secret scan errors: " + "; ".join(report.errors))
    return report, "\n".join(lines)


def _pause_decision_clock(last_progress_at: float, waited_seconds: float) -> float:
    return float(last_progress_at) + max(0.0, float(waited_seconds or 0.0))


def _run_external_work_wait(
    *,
    workdir: Path,
    work_id: str,
    keep_waiting: Callable[[], bool],
) -> tuple[str, float]:
    """Wait on external work until it changes state or ``keep_waiting`` says stop.

    Waiting writes no events: it used to log a started/completed pair every
    two minutes for as long as a job ran. What the wait led to -- the next
    round, or the mission's pause -- is what the journal records.
    """
    waited = 0.0
    while True:
        try:
            reason, seconds = wait_for_external_work_cadence(workdir, work_id)
        except Exception as exc:  # noqa: BLE001
            reason, seconds = f"error:{type(exc).__name__}", 0.0
        waited += seconds
        if reason != "cadence_elapsed" or process_stop.stop_requested() or not keep_waiting():
            return reason, waited


__all__ = [
    "_apply_round_secret_guard",
    "_pause_decision_clock",
    "_review_event_payload",
    "_run_external_work_wait",
]
