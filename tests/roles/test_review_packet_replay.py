"""The review packet of s-efe9deac call a8447ec8 (#194), rebuilt from its snapshots.

At 09:40 UTC on 2026-10-27 the Reviewer of mission 5665c4cc565d read 118KB.
Its workspace diff spent 60 lines on RESEARCH_NOTES.md, whose current text the
prompt already carried whole, and listed single files under results/ (5575 of
the 7021 changed paths) where the method card already counts them. The card
called all eight components "no code anchor" although each row names its
entry point, and the pointer to the event log masked the Engineer's call id.

The fixtures are the snapshot store's files for that review (ce661060 ->
a75caf4b): METHOD.md as it was (cut to its first 44 lines, which hold the
round's one change), the research notes, one new config and 133 of the
changed paths under results/.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from argus.core.workspace_snapshot import WorkspaceSnapshots
from argus.roles.prompts.reviewer import assemble_reviewer_prompt, render_reviewer_prompt
from argus.roles.task_contract import EXPERIMENT_EVIDENCE_CONTRACT

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac"
PACKET = FIXTURES / "review_packet"
ENGINEER_CALL = "6d04a40b653a4ef6b5f2a34f0ad5246f"


def _write(workspace: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _prompt(tmp_path: Path) -> str:
    state, workspace = tmp_path / "state", tmp_path / "workspace"
    (state / ".argus").mkdir(parents=True)
    shutil.copy(FIXTURES / "hard_gates" / "PIPELINE_STATE.json", state / ".argus" / "PIPELINE_STATE.json")
    mission = json.loads((FIXTURES / "hard_gates" / "mission_5665c4cc565d.json").read_text(encoding="utf-8"))
    snapshots = WorkspaceSnapshots(store=state / "snapshots.git", workdir=workspace, line="shared_vllm")
    _write(workspace, {
        "METHOD.md": (PACKET / "METHOD.base.md").read_text(encoding="utf-8"),
        "RESEARCH_NOTES.md": (PACKET / "RESEARCH_NOTES.base.md").read_text(encoding="utf-8"),
    })
    assert snapshots.take("baseline")
    _write(workspace, {
        "METHOD.md": (PACKET / "METHOD.candidate.md").read_text(encoding="utf-8"),
        "RESEARCH_NOTES.md": (PACKET / "RESEARCH_NOTES.candidate.md").read_text(encoding="utf-8"),
        "configs/infrastructure/shared-vllm/hf.yaml": (PACKET / "hf.yaml").read_text(encoding="utf-8"),
        **{path: "x\n" for path in (PACKET / "results.txt").read_text(encoding="utf-8").split()},
    })
    assert snapshots.take("candidate")
    owner = SimpleNamespace(skill_store=None, mission=None, _last_prompt_block_stats={})
    return assemble_reviewer_prompt(*render_reviewer_prompt(
        owner,
        objective=mission["objective"],
        original_objective=mission["original_objective"],
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=mission["id"],
        main_summary=mission["title"],
        main_error=None,
        scope="bounded",
        engineer_log_path=str(state / "events.jsonl"),
        engineer_call_id=ENGINEER_CALL,
        working_dir=workspace,
        vertical_state_root=state,
        vertical="research",
        preselected_skill_block="",
        snapshots=snapshots,
    ))


def test_the_packet_keeps_the_method_change_and_drops_what_the_prompt_already_has(tmp_path) -> None:
    prompt = _prompt(tmp_path)
    changes = prompt.split("## Workspace changes under review", 1)[1]

    assert "\n+- Engine migration (2026-10-27): " in changes
    assert "--- configs/infrastructure/shared-vllm/hf.yaml" in changes
    assert "--- RESEARCH_NOTES.md" not in changes
    assert "- results/infrastructure/shared-vllm/diagnostic/: 54 files, +54 -0\n" in changes
    assert "- results/infrastructure/shared-vllm/hf/: 78 files, +78 -0\n" in changes
    assert "attempt-001/allocation.json" not in changes


def test_the_method_card_and_the_rubric_carry_no_host_only_rules(tmp_path) -> None:
    prompt = _prompt(tmp_path)

    assert "\n- budget_broker\n" in prompt  # an untested component stays a required repair
    assert "no test carries its marker: execution_verdicts, budget_broker" in prompt
    assert "anchor" not in prompt and "without a '# why'" not in prompt
    assert "Hyperparameters changed" not in prompt
    assert "## Opaque integrity IDs" not in prompt
    assert "## Integrity of the historical record" not in prompt
    assert prompt.count(EXPERIMENT_EVIDENCE_CONTRACT) == 1  # H1, the copy's one home
    assert "result_class: known_result" not in prompt
    assert f"(current call `{ENGINEER_CALL}`)" in prompt


def test_the_stand_in_scan_reads_research_and_skips_a_nested_workspace(tmp_path) -> None:
    """s-efe9deac keeps its code in research/, a directory without __init__.py, and
    an Argus maintenance task's workspace (59,780 Python files) in maintenance/."""
    from argus.verticals.research.method_card import render_for_reviewer

    workspace = tmp_path / "workspace"
    _write(workspace, {
        "METHOD.md": (PACKET / "METHOD.candidate.md").read_text(encoding="utf-8"),
        "research/group_verification/lb_reference.py": (PACKET / "lb_reference_main.txt").read_text(encoding="utf-8"),
        "maintenance/planner-wait-repair/.argus/state.json": "{}",
        "maintenance/planner-wait-repair/regression.py": "def mock_reviewer():\n    return 'done'\n",
    })

    packet = render_for_reviewer(workspace)

    assert "- research/group_verification/lb_reference.py:24 # why: all stand-in verdicts" in packet
    assert "maintenance/" not in packet
