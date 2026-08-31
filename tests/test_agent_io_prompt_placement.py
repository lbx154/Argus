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

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.adapters.agent_cli_backend._io_log import raw_transcript_path


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_raw_transcript_is_a_sibling_of_the_history_log() -> None:
    assert raw_transcript_path(Path("/p/events.jsonl")) == Path("/p/agent_io.jsonl")
    assert raw_transcript_path(None) is None


class _Backend:
    """Captures what the real spawn path writes, per destination."""

    def __init__(self) -> None:
        self.written: dict[str, list[dict]] = {}
        self._runner = SimpleNamespace(backend="copilot")

    def _log_agent_io(self, path, row) -> None:
        self.written.setdefault(str(path), []).append(dict(row))


def _emit_start(prompt: str, io_mode: str, tmp_path: Path) -> tuple[list[dict], list[dict]]:
    """Call the REAL start-record path and return (history rows, raw rows)."""
    from argus_skill.adapters.agent_cli_backend._exec_spawn import log_start_record

    backend = _Backend()
    history = tmp_path / "events.jsonl"
    ctx = SimpleNamespace(
        call_id="c1",
        run_label="engineer-1",
        options=SimpleNamespace(model="m", reasoning_effort="high", working_dir="/w"),
        resume_thread_id=None,
        io_mode=io_mode,
        prompt=prompt,
        log_path=history,
    )

    log_start_record(backend, ctx)

    return (
        backend.written.get(str(history), []),
        backend.written.get(str(raw_transcript_path(history)), []),
    )


@pytest.mark.parametrize("io_mode", ["full", "compact"])
def test_history_never_carries_the_prompt(io_mode: str, tmp_path: Path) -> None:
    prompt = "solve the conjecture\n" * 500
    history, _raw = _emit_start(prompt, io_mode, tmp_path)

    assert history and "prompt" not in history[0]
    assert history[0]["prompt_chars"] == len(prompt)


def test_full_mode_keeps_the_verbatim_prompt_in_the_raw_transcript(
    tmp_path: Path,
) -> None:
    prompt = "solve the conjecture\n" * 500
    history, raw = _emit_start(prompt, "full", tmp_path)

    assert raw and raw[0]["prompt"] == prompt
    assert history[0]["call_id"] == raw[0]["call_id"]
    assert "prompt_sha256" not in history[0]
    assert "prompt_sha256" not in raw[0]


def test_compact_mode_writes_nothing_to_the_raw_transcript(tmp_path: Path) -> None:
    # Compact mode exists to not keep the prompt at all; the split must not
    # smuggle it into a second file.
    _history, raw = _emit_start("short prompt", "compact", tmp_path)

    assert raw == []


def test_the_history_record_stays_small(tmp_path: Path) -> None:
    # The point of the change, as a number: the record the projections scan is
    # bounded by its metadata, not by prompt length.
    small, _ = _emit_start("x" * 100, "full", tmp_path)
    large, _ = _emit_start("x" * 500_000, "full", tmp_path)

    assert len(json.dumps(large[0])) - len(json.dumps(small[0])) < 200


def test_no_consumer_reads_the_prompt_off_an_event_row() -> None:
    # Reverse assertion. Moving the field is only safe while nothing reads it;
    # if a reader appears, this fails and the split has to be revisited.
    repo = Path(__file__).resolve().parents[1]
    hits = [
        f"{path}:{line_number}:{line}"
        for path in (repo / "argus_skill").rglob("*.py")
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


def test_the_prompt_is_redacted_on_its_way_to_the_raw_transcript(
    tmp_path: Path,
) -> None:
    """Moving the prompt must not move it around the credential guard.

    Both destinations go through ``_log_agent_io`` -> ``IoLogger.log`` ->
    ``redact_secrets_record``, but that is exactly the kind of thing worth
    asserting rather than assuming: the new write is the one that carries the
    verbatim prompt, so it is the one a leaked key would ride out on.

    The secret is supplied as a *known* value, which is how production reaches
    this path: `known_secret_values()` collects the configured environment and
    vault credentials and redacts them by exact match. Guessing a credential
    from the shape of its value was deliberately removed, so a bare token shape
    is no longer what proves the guard runs — an actual known secret is.
    """
    from argus_skill.adapters.agent_cli_backend._exec_spawn import log_start_record
    from argus_skill.adapters.agent_cli_backend._io_log import AgentIOLogger

    secret = "-".join(("sk", "proj", "AbCd1234EfGh5678IjKl"))
    history = tmp_path / "events.jsonl"

    class _RealLoggingBackend:
        def __init__(self) -> None:
            self._runner = SimpleNamespace(backend="copilot")
            self._io_logger = AgentIOLogger()
            self._known_secret_values = (secret,)

        def _log_agent_io(self, path, row) -> None:
            self._io_logger.log(path, row, known_secret_values=self._known_secret_values)

    ctx = SimpleNamespace(
        call_id="c1",
        run_label="engineer-1",
        options=SimpleNamespace(model="m", reasoning_effort="high", working_dir="/w"),
        resume_thread_id=None,
        io_mode="full",
        prompt=f"use this key: {secret}\n",
        log_path=history,
    )

    log_start_record(_RealLoggingBackend(), ctx)

    raw = raw_transcript_path(history)
    assert raw.exists(), "the verbatim prompt was never written"
    assert secret not in raw.read_text(encoding="utf-8")
    assert secret not in history.read_text(encoding="utf-8")
