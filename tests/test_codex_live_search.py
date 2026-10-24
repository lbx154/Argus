"""codex live web_search (idea-stage) wiring — the ``web_search="live"`` feature.

Idea discovery runs in the research stage; there the engineer enables codex's
native live web_search so literature grounding is real, not cached/recalled.
These tests pin the four links of the chain:
  1. RunnerOptions.live_search -> ``-c web_search="live"`` in the codex command
  2. the core->agent_cli options translation carries the flag
  3. the research-stage gate turns it on for research, off elsewhere
  4. the ACTIVE VERTICAL owns which of its own stages get it; the framework
     default (research) only applies to a vertical that declares nothing
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.agent_cli_runner import RunnerOptions as AcOpts
from argus.core.models import RunnerOptions as CoreOpts
from argus.engineer.runner import (
    DEFAULT_LIVE_SEARCH_STAGES,
)
from argus.skills.vertical_select import persist_vertical
from tests.hand_off import hand_off

pytestmark = pytest.mark.usefixtures("optimize_vertical")


def _cmd(live: bool) -> list[str]:
    r = AgentCliRunner(agent_bin="codex")
    return r._build_codex_command(
        resume_thread_id=None,
        options=AcOpts(model="gpt-5.5", live_search=live, full_auto=True),
    )


def test_search_flag_present_only_when_live():
    assert any('web_search="live"' in a for a in _cmd(True))
    assert not any('web_search="live"' in a for a in _cmd(False))


# --- the active vertical owns its own live-search stages -------------------


def _optimize_project(tmp_path: Path) -> Path:
    persist_vertical(tmp_path, "optimize_lab")
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["current_stage"] = "optimize"
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def _done_review() -> CannedResponse:
    return CannedResponse(action=hand_off("done", "Verified."))


def _build_loop(
    tmp_path: Path,
    vertical: str,
    *,
    live_search_stages: frozenset[str] | None = None,
    vertical_state_root: Path | None = None,
) -> tuple[SkillLoop, MemoryBackend]:
    """Build a real SkillLoop, optionally with a caller-configured baseline."""
    skills = tmp_path / "skills"
    skills.mkdir()
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Worked and verified."))
    backend.queue("reviewer", _done_review())
    loop = SkillLoop(
        skills_dir=skills,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=1,
            workflow_mode="direct",
            active_vertical=vertical,
            vertical_state_root=vertical_state_root,
        ),
    )
    if live_search_stages is not None:
        # Exactly what a caller does through the public EngineerConfig knob.
        loop.supervised.engineer_config.live_search_stages = live_search_stages
    return loop, backend


def _engineer_options(backend: MemoryBackend) -> CoreOpts:
    return next(
        options for label, _prompt, options in backend.history
        if label == "engineer-r1"
    )


def _run_one_round(
    tmp_path: Path,
    vertical: str,
    *,
    live_search_stages: frozenset[str] | None = None,
    vertical_state_root: Path | None = None,
    task: str = "Do the work.",
) -> CoreOpts:
    """Run one real SkillLoop round and return the engineer's RunnerOptions."""
    loop, backend = _build_loop(
        tmp_path,
        vertical,
        live_search_stages=live_search_stages,
        vertical_state_root=vertical_state_root,
    )
    loop.run(
        task,
        workdir=tmp_path,
        scope="bounded",
    )
    return _engineer_options(backend)


def test_separated_state_root_enables_optimize_live_search(
    tmp_path: Path,
) -> None:
    """The contract and stage gate must read the same production state root."""
    state_root = _optimize_project(tmp_path / "state")
    execution_root = tmp_path / "execution"
    persist_vertical(execution_root, "research")

    options = _run_one_round(
        execution_root,
        "optimize_lab",
        vertical_state_root=state_root,
    )

    # State is optimize, while the execution tree is research. Reading the
    # execution tree would incorrectly disable the optimize-only declaration.
    assert options.live_search is True


@pytest.mark.parametrize(
    ("custom", "expected"),
    [
        (frozenset({"optimize"}), True),
        (frozenset(), False),
    ],
)
def test_optimize_default_does_not_replace_caller_live_search_config(
    tmp_path: Path,
    custom: frozenset[str],
    expected: bool,
) -> None:
    _optimize_project(tmp_path)

    options = _run_one_round(
        tmp_path,
        "optimize_lab",
        live_search_stages=custom,
    )

    assert options.live_search is expected


def test_replaced_default_live_search_config_overrides_optimize_default(
    tmp_path: Path,
) -> None:
    _optimize_project(tmp_path)
    loop, backend = _build_loop(tmp_path, "optimize_lab")
    loop.supervised.engineer_config = loop.supervised.engineer_config.replace(
        live_search_stages=DEFAULT_LIVE_SEARCH_STAGES,
    )

    loop.run(
        "Do the work.",
        workdir=tmp_path,
        scope="bounded",
    )

    assert _engineer_options(backend).live_search is False
