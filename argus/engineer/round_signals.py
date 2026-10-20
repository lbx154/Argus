"""Small event and secret-guard helpers for Engineer rounds."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..core.event_catalog import EventType
from ..core.models import ReviewDecision
from ..core.secret_guard import (
    SecretScrubReport,
    known_secret_values,
    redact_secrets_record,
    scrub_recent_text_artifacts,
)


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


__all__ = [
    "_apply_round_secret_guard",
    "_review_event_payload",
]
