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


# --- loop-level: the research-stage hook records events on the stream --------

def test_loop_emits_idea_search_events(tmp_path):
    """SkillLoop must record the codex-web-search candidate seeding on its event
    stream (cockpit / --follow / events.jsonl), not just via python logging."""
    import json

    from argus_skill import SkillLoop, SkillLoopConfig
    from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend

    # Force the research stage so the hook fires.
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "research", "current_stage": "research"}),
        encoding="utf-8",
    )
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=""))
    # the codex idea-search candidate source
    backend.queue("idea-search", CannedResponse(message=_CANDIDATES))
    backend.queue("engineer-r1", CannedResponse(message="Wrote research brief; done."))
    backend.queue("reviewer", CannedResponse(message=json.dumps({
        "status": "done",
        "reason": "Ideas produced.",
        "next_action": "none",
        "round_summary_markdown": "# Review\n\n- ok\n",
        "completion_summary_markdown": "Done.",
    })))

    events: list = []
    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=2, paper_mission=True),
        on_event=events.append,
    )
    loop.run("detect unfaithful chain-of-thought reasoning", workdir=tmp_path)

    types = [e.get("type") for e in events]
    assert "idea.search.started" in types
    completed = [e for e in events if e.get("type") == "idea.search.completed"]
    assert completed and completed[0]["count"] == 2

    # the seeded candidates actually landed in the pool
    text = _read_candidates(str(tmp_path))
    assert SOURCE_MARKER in text and "## Candidate WS-1" in text

    # idea-search ran with live_search=True
    labels = [lbl for lbl, _p, _o in backend.history]
    assert "idea-search" in labels
    opts = next(o for lbl, _p, o in backend.history if lbl == "idea-search")
    assert getattr(opts, "live_search", False) is True
    assert opts.working_dir == str(tmp_path.resolve())
    assert opts.full_auto is True


def test_loop_idea_search_run_once_no_reemit(tmp_path):
    """A second research-stage pass must NOT re-run codex or re-emit started."""
    import json

    from argus_skill import SkillLoop, SkillLoopConfig
    from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend

    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": "research"}), encoding="utf-8",
    )
    # pre-seed the marker -> _already_seeded is True
    (tmp_path / "research" / "IDEA_CANDIDATES.md").write_text(
        f"{SOURCE_MARKER}\n## Candidate WS-1: prior\n", encoding="utf-8",
    )

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=""))
    backend.queue("engineer-r1", CannedResponse(message="done."))
    backend.queue("reviewer", CannedResponse(message=json.dumps({
        "status": "done", "reason": "x", "next_action": "none",
        "round_summary_markdown": "# r\n", "completion_summary_markdown": "d",
    })))

    events: list = []
    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=2, paper_mission=True),
        on_event=events.append,
    )
    loop.run("detect unfaithful CoT", workdir=tmp_path)

    assert "idea.search.started" not in [e.get("type") for e in events]
    assert "idea-search" not in [lbl for lbl, _p, _o in backend.history]


def test_loop_skips_idea_search_for_a_non_research_vertical_sharing_the_stage_name(
    tmp_path,
):
    """Regression: the optimize-family verticals (kernelbench/speedrun/nanochat/
    nanogpt_speedrun) also name their FIRST stage "research" (see each
    vertical's own ``STAGE_ORDER``) — that is a shared STAGE name, not the
    "research" (paper) VERTICAL. The paper-ideation hook ("candidate discovery
    for a paper") must not fire just because ``current_stage == "research"``;
    it must also confirm the persisted VERTICAL is actually "research"."""
    import json

    from argus_skill import SkillLoop, SkillLoopConfig
    from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend

    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "kernelbench", "current_stage": "research"}),
        encoding="utf-8",
    )

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=""))
    backend.queue("engineer-r1", CannedResponse(message="wrote ground truth; done."))
    backend.queue("reviewer", CannedResponse(message=json.dumps({
        "status": "done", "reason": "x", "next_action": "none",
        "round_summary_markdown": "# r\n", "completion_summary_markdown": "d",
    })))

    events: list = []
    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=2, paper_mission=True),
        on_event=events.append,
    )
    loop.run("maximize SOL score on SOL-ExecBench kernels", workdir=tmp_path)

    assert "idea.search.started" not in [e.get("type") for e in events]
    assert "idea-search" not in [lbl for lbl, _p, _o in backend.history]
    assert _read_candidates(str(tmp_path)) == ""


def test_loop_skips_idea_search_when_paper_mode_is_not_explicit(tmp_path):
    """A stale/default research state is insufficient: ordinary bounded work
    must not spend a live-search call unless mission typing positively enabled
    the paper pipeline."""
    import json

    from argus_skill import SkillLoop, SkillLoopConfig
    from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend

    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "research", "current_stage": "research"}),
        encoding="utf-8",
    )

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=""))
    backend.queue("engineer-r1", CannedResponse(message="done."))
    backend.queue("reviewer", CannedResponse(message=json.dumps({
        "status": "done", "reason": "x", "next_action": "none",
        "round_summary_markdown": "# r\n", "completion_summary_markdown": "d",
    })))

    events: list = []
    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=2, paper_mission=False),
        on_event=events.append,
    )
    loop.run("build a bounded JSONL verifier", workdir=tmp_path)

    assert "idea.search.started" not in [e.get("type") for e in events]
    assert "idea-search" not in [label for label, _prompt, _opts in backend.history]
