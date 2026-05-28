from __future__ import annotations

from dataclasses import dataclass

from .codex_runner import InactivitySnapshot


@dataclass
class StallDecision:
    should_restart: bool
    reason: str
    matched_pattern: str | None = None


ERROR_PATTERNS: list[tuple[str, str]] = [
    (
        "stream disconnected before completion",
        "Detected stream disconnect before completion; likely transient transport failure.",
    ),
    (
        "an error occurred while processing your request",
        "Detected upstream request-processing error from model provider.",
    ),
    (
        "you can retry your request",
        "Detected explicit retry recommendation from upstream error message.",
    ),
    (
        "failed to shutdown rollout recorder",
        "Detected rollout recorder shutdown error in Codex runtime.",
    ),
    (
        "channel closed",
        "Detected closed internal channel, suggesting stalled/broken session state.",
    ),
    (
        "unexpected status 5",
        "Detected upstream 5xx service error.",
    ),
    (
        "timed out",
        "Detected timeout error in recent output.",
    ),
]


def analyze_stall(snapshot: InactivitySnapshot) -> StallDecision:
    haystack = _normalized_text(
        "\n".join(
            [
                snapshot.last_agent_message,
                *snapshot.stdout_tail,
                *snapshot.stderr_tail,
            ]
        )
    )
    for pattern, reason in ERROR_PATTERNS:
        if pattern in haystack:
            return StallDecision(
                should_restart=True,
                reason=reason,
                matched_pattern=pattern,
            )
    return StallDecision(
        should_restart=False,
        reason=(
            f"No explicit error signature found after {int(snapshot.idle_seconds)}s idle. "
            "Continue waiting; task may still be making progress without frequent output."
        ),
    )


def _normalized_text(text: str) -> str:
    return " ".join(text.lower().split())
