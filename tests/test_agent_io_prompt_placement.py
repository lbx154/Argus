"""Where the provider prompt is stored, and where it is not.

`events.jsonl` is the authoritative project history: the journal, mission view
and campaign tally are projections of it. `agent_io.jsonl` beside it is the
verbatim provider transcript — a debug artifact, and the one that ring-rotates.

The full prompt was being written into the history log. Measured on a real
project, `agent.io.start` was 63% of its bytes (47.3 MB of 74.9 MB) purely
because it carried the prompt, and nothing reads that field: the Web UI drops
the event type outright, `usage.py` takes only `call_id`, `event_log.py` only
tests that the type occurs. The history paid three times its own content for a
field with no reader, and every projection had to scan past it.

These tests pin the split. The existing call ID links the compact history row
to its verbatim raw transcript record.
"""

from __future__ import annotations

from pathlib import Path

from argus.adapters.agent_cli_backend._io_log import raw_transcript_path


def test_raw_transcript_is_a_sibling_of_the_history_log() -> None:
    assert raw_transcript_path(Path("/p/events.jsonl")) == Path("/p/agent_io.jsonl")
    assert raw_transcript_path(None) is None


def test_no_consumer_reads_the_prompt_off_an_event_row() -> None:
    # Reverse assertion. Moving the field is only safe while nothing reads it;
    # if a reader appears, this fails and the split has to be revisited.
    repo = Path(__file__).resolve().parents[1]
    hits = [
        f"{path}:{line_number}:{line}"
        for path in (repo / "argus").rglob("*.py")
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        )
        if 'get("prompt")' in line
    ]
    offenders = [
        line
        for line in hits
        if "image_api" not in line and "figure_tool" not in line
    ]
    assert offenders == [], offenders
