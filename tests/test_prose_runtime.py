"""prose runtime capstone: registered + all four shared contracts + the
structure-check gate enforce at RUN TIME via STAGE_CHECKS (subprocess).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from argus_skill.skills.vertical_select import VERTICALS
from argus_skill.verticals.prose.artifacts import build_prose_manifest
from argus_skill.verticals.prose.stages import STAGE_CHECKS

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DRAFT = "灶台还在那里。\n\n光从窗格里斜下来，落在她的手背上。\n\n后来老屋拆了。"
_STATE = {
    "narrative_center": "祖母的厨房",
    "observation_subject": "灶台与光",
    "factual_anchors": ["1998年"],
    "memory_boundary": "气味是回忆，年份是事实",
    "paragraph_movement": "从物到人到时间",
    "ending_strategy": "以动作收束",
    "spec": {"language": "zh", "min_paragraphs": 3},
}


def _all_cmds():
    return [cmd for checks in STAGE_CHECKS.values() for _d, cmd in checks]


def _cmd(needle: str) -> str:
    hits = [c for c in _all_cmds() if needle in c]
    assert hits, f"no STAGE_CHECK wires {needle!r}"
    return hits[0]


def _run(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    cmd = cmd.replace("{python}", sys.executable)
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    return subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True, text=True, env=env)


def _write(base: Path, rel: str, obj) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj, encoding="utf-8"
    )


def test_vertical_is_registered():
    assert "prose" in VERTICALS


def test_all_four_contracts_plus_structure_are_wired():
    assert _cmd("intake-validate")
    assert _cmd("review-validate")
    assert _cmd("check-plan")
    assert _cmd("manifest-validate")
    assert _cmd("manifest-content")
    assert _cmd("source-registry")
    assert _cmd("check-usage")
    assert _cmd("structure-check prose/draft.md")


def test_runtime_structure_gate_passes_complete_fails_incomplete(tmp_path):
    cmd = _cmd("structure-check prose/draft.md")
    _write(tmp_path, "prose/draft.md", _DRAFT)
    _write(tmp_path, "prose/prose_state.json", _STATE)
    assert _run(cmd, tmp_path).returncode == 0
    # drop a required prose_state field -> the stage FAILS
    bad = dict(_STATE)
    del bad["memory_boundary"]
    _write(tmp_path, "prose/prose_state.json", bad)
    assert _run(cmd, tmp_path).returncode != 0
    # a banned word declared in spec -> fail
    banned = dict(_STATE)
    banned["spec"] = {"language": "zh", "banned_words": ["光"]}
    _write(tmp_path, "prose/prose_state.json", banned)
    assert _run(cmd, tmp_path).returncode != 0


def test_runtime_intake_gate(tmp_path):
    cmd = _cmd("intake-validate")
    _write(
        tmp_path,
        "prose/task_envelope.json",
        {
            "task_id": "pr1",
            "mode": "from_scratch",
            "language": "zh",
            "form": "抒情散文",
            "intent": "写一篇散文",
        },
    )
    assert _run(cmd, tmp_path).returncode == 0
    _write(
        tmp_path,
        "prose/task_envelope.json",
        {
            "task_id": "pr1",
            "mode": "from_scratch",
            "language": "zh",
            "form": "short_story",
            "intent": "x",
        },
    )
    assert _run(cmd, tmp_path).returncode != 0


def test_runtime_registry_review_usage_manifest(tmp_path):
    assert _run(_cmd("source-registry"), tmp_path).returncode == 0

    rv = _cmd("review-validate")
    _write(
        tmp_path,
        "prose/review.json",
        {
            "verdict": "revise",
            "findings": [
                {
                    "id": "f1",
                    "type": "fact_memory",
                    "severity": "major",
                    "blocking": False,
                    "location": "p2",
                    "evidence": "blurs fact and memory",
                    "suggested_action": "separate",
                }
            ],
        },
    )
    assert _run(rv, tmp_path).returncode == 0

    cu = _cmd("check-usage")
    _write(tmp_path, "prose/source_usage.json", {"task_id": "pr1", "uses": []})
    assert _run(cu, tmp_path).returncode == 0
    _write(
        tmp_path,
        "prose/source_usage.json",
        {
            "task_id": "pr1",
            "uses": [
                {
                    "use_id": "u1",
                    "source_id": "ghost",
                    "use": "query_only",
                    "stage": "draft",
                    "consumed_by": "draft",
                }
            ],
        },
    )
    assert _run(cu, tmp_path).returncode != 0

    mv = _cmd("manifest-validate")
    _write(tmp_path, "prose/artifact_manifest.json", build_prose_manifest("pr1"))
    assert _run(mv, tmp_path).returncode == 0
