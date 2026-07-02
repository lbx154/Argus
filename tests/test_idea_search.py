"""idea_search: codex web-search as an ADDITIONAL candidate SOURCE.

Pins the contract the research-stage hook relies on:
  1. web-search candidates are APPENDED (never overwrite) under the provenance
     marker, in ``## Candidate`` format, and the count is returned;
  2. the marker is a run-once guard — a second call is a no-op;
  3. the codex call is made with ``live_search=True`` (real live web_search);
  4. every failure mode fails OPEN (returns 0, never raises) so a candidate
     source can never block the loop.
"""
from __future__ import annotations

import os
import tempfile

from argus_skill.core.models import RunnerResult
from argus_skill.skills.idea_search import (
    SOURCE_MARKER,
    _already_seeded,
    augment_idea_candidates,
)

_CANDIDATES = """## Candidate WS-1: attention sinks explain length drift

**Grounding**: Real Paper (2025), arXiv:2501.00001.

## Candidate WS-2: entropy gate for early exit

**Grounding**: Other Paper (2025), arXiv:2502.00002.
"""


class _FakeRunner:
    """Records the options handed to run_exec and returns a canned result."""

    def __init__(self, message: str = _CANDIDATES, exit_code: int = 0, raises: bool = False):
        self._message = message
        self._exit_code = exit_code
        self._raises = raises
        self.calls: list = []

    def run_exec(self, *, prompt, options, run_label=None, **_kw):
        self.calls.append(options)
        if self._raises:
            raise RuntimeError("boom")
        return RunnerResult(
            exit_code=self._exit_code,
            agent_messages=[self._message] if self._message else [],
        )


def _workdir() -> str:
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "research"), exist_ok=True)
    return d


def _read_candidates(workdir: str) -> str:
    p = os.path.join(workdir, "research", "IDEA_CANDIDATES.md")
    return open(p, encoding="utf-8").read() if os.path.isfile(p) else ""


def test_appends_under_marker_and_returns_count():
    d = _workdir()
    n = augment_idea_candidates(_FakeRunner(), d, direction="length drift in LLMs")
    assert n == 2
    text = _read_candidates(d)
    assert SOURCE_MARKER in text
    assert "## Candidate WS-1" in text and "## Candidate WS-2" in text


def test_preserves_existing_candidates():
    d = _workdir()
    existing = "## Candidate I-1: pre-existing argus idea\n"
    with open(os.path.join(d, "research", "IDEA_CANDIDATES.md"), "w") as fh:
        fh.write(existing)
    augment_idea_candidates(_FakeRunner(), d, direction="x")
    text = _read_candidates(d)
    assert existing.strip() in text  # original block untouched
    assert SOURCE_MARKER in text  # web-search block appended after it


def test_run_once_guard():
    d = _workdir()
    r = _FakeRunner()
    assert augment_idea_candidates(r, d, direction="x") == 2
    assert _already_seeded(d) is True
    # second call is a no-op: no re-run, no re-append
    assert augment_idea_candidates(r, d, direction="x") == 0
    assert len(r.calls) == 1
    assert _read_candidates(d).count(SOURCE_MARKER) == 1


def test_codex_call_uses_live_search():
    d = _workdir()
    r = _FakeRunner()
    augment_idea_candidates(r, d, direction="x", model="gpt-5.5")
    assert len(r.calls) == 1
    opts = r.calls[0]
    assert getattr(opts, "live_search", False) is True
    assert opts.model == "gpt-5.5"


def test_fail_open_on_runner_exception():
    d = _workdir()
    assert augment_idea_candidates(_FakeRunner(raises=True), d, direction="x") == 0
    assert _read_candidates(d) == ""  # nothing written


def test_fail_open_on_nonzero_exit():
    d = _workdir()
    assert augment_idea_candidates(_FakeRunner(exit_code=1), d, direction="x") == 0
    assert _read_candidates(d) == ""


def test_no_candidates_in_output_is_noop():
    d = _workdir()
    assert augment_idea_candidates(_FakeRunner(message="sorry, nothing"), d, direction="x") == 0
    assert _read_candidates(d) == ""


def test_empty_direction_and_no_brief_skips():
    d = _workdir()
    r = _FakeRunner()
    # no direction passed, no RESEARCH_BRIEF.md -> skip without calling codex
    assert augment_idea_candidates(r, d, direction=None) == 0
    assert len(r.calls) == 0


def test_direction_falls_back_to_brief():
    d = _workdir()
    with open(os.path.join(d, "research", "RESEARCH_BRIEF.md"), "w") as fh:
        fh.write("# Brief\n\nStudy length drift in long-context decoding.\n")
    r = _FakeRunner()
    assert augment_idea_candidates(r, d, direction=None) == 2
    assert len(r.calls) == 1


def test_none_runner_fails_open():
    d = _workdir()
    assert augment_idea_candidates(None, d, direction="x") == 0
