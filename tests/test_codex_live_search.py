"""codex live web_search (idea-stage) wiring — the ``web_search="live"`` feature.

Idea discovery runs in the research stage; there the engineer enables codex's
native live web_search so literature grounding is real, not cached/recalled.
These tests pin the three links of the chain:
  1. RunnerOptions.live_search -> ``-c web_search="live"`` in the codex command
  2. the core->agent_cli options translation carries the flag
  3. the research-stage gate turns it on for research, off elsewhere
"""
from __future__ import annotations

import json
import os
import tempfile

from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner
from argus_skill.agent_cli.agent_cli_runner import RunnerOptions as AcOpts
from argus_skill.core.models import RunnerOptions as CoreOpts
from argus_skill.engineer.runner import _engineer_live_search


def _cmd(live: bool) -> list[str]:
    r = AgentCliRunner(agent_bin="codex")
    return r._build_codex_command(
        resume_thread_id=None,
        options=AcOpts(model="gpt-5.5", live_search=live, full_auto=True),
    )


def test_search_flag_present_only_when_live():
    assert any('web_search="live"' in a for a in _cmd(True))
    assert not any('web_search="live"' in a for a in _cmd(False))


def test_core_and_agentcli_both_have_field():
    assert CoreOpts(model="gpt-5.5", live_search=True).live_search is True
    assert CoreOpts().live_search is False  # default off
    assert "live_search" in AcOpts.__dataclass_fields__


def test_stage_gate_research_on_others_off():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "research"), exist_ok=True)
    stages = frozenset({"research"})

    def _set(stage: str) -> None:
        with open(os.path.join(d, "research", "PIPELINE_STATE.json"), "w") as fh:
            json.dump({"current_stage": stage}, fh)

    _set("research")
    assert _engineer_live_search(d, stages) is True
    _set("plan")
    assert _engineer_live_search(d, stages) is False
    _set("run")
    assert _engineer_live_search(d, stages) is False


def test_stage_gate_empty_stages_never_on():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "research"), exist_ok=True)
    with open(os.path.join(d, "research", "PIPELINE_STATE.json"), "w") as fh:
        json.dump({"current_stage": "research"}, fh)
    assert _engineer_live_search(d, frozenset()) is False
