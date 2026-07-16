"""Runtime closed-loop test: the fiction STYLE + TEMPORAL gates enforce at RUN
TIME via STAGE_CHECKS (subprocess), exactly as the stage-check runner does.

Unlike the reviewer's heuristic craft notes, these gates recompute from the files
on disk, so their verdict cannot be faked. If the wiring in stages.py is removed,
the `_checks_with` lookups return [] and these tests fail.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from argus_skill.verticals.fiction_writing.stages import STAGE_CHECKS

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _checks_with(stage: str, needle: str) -> list[str]:
    return [cmd for _desc, cmd in STAGE_CHECKS[stage] if needle in cmd]


def _run(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    cmd = cmd.replace("{python}", sys.executable)
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    return subprocess.run(cmd, shell=True, cwd=str(cwd),
                          capture_output=True, text=True, env=env)


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj,
                    encoding="utf-8")


# --------------------------------------------------------------------------- #
# wiring: the three new gates are actually registered on their stages
# --------------------------------------------------------------------------- #
def test_intake_wires_voice_card_validation():
    assert _checks_with("intake", "validate-style")


def test_review_wires_style_lint_and_temporal():
    assert _checks_with("review", "style-lint")
    assert _checks_with("review", "temporal-check")


# --------------------------------------------------------------------------- #
# validate-style gate
# --------------------------------------------------------------------------- #
def test_runtime_validate_style_passes_thin_fails_malformed(tmp_path):
    cmd = _checks_with("intake", "validate-style")[0]
    _write(tmp_path / "fiction" / "style_profile.json", {})
    assert _run(cmd, tmp_path).returncode == 0
    _write(tmp_path / "fiction" / "style_profile.json", {"meta": {"register": "modern"}})
    assert _run(cmd, tmp_path).returncode != 0


# --------------------------------------------------------------------------- #
# style-lint gate: blocks ONLY on a declared forbidden term
# --------------------------------------------------------------------------- #
def test_runtime_style_lint_passes_clean_fails_forbidden(tmp_path):
    cmd = _checks_with("review", "style-lint")[0]
    _write(tmp_path / "fiction" / "creative_brief.json", {"language": "zh"})
    _write(tmp_path / "fiction" / "style_profile.json",
           {"meta": {"language": "zh"}, "forbidden_lexicon": ["手机"]})
    # clean draft (no forbidden term, cliché notes don't gate) -> pass
    _write(tmp_path / "fiction" / "draft.md", "他推开门，屋里没有开灯。桌上放着一只碗。")
    assert _run(cmd, tmp_path).returncode == 0
    # a forbidden term present -> the stage FAILS
    _write(tmp_path / "fiction" / "draft.md", "他掏出手机看了一眼。")
    assert _run(cmd, tmp_path).returncode != 0


# --------------------------------------------------------------------------- #
# temporal-check gate: fails on a deterministic age/year contradiction
# --------------------------------------------------------------------------- #
def test_runtime_temporal_check_passes_consistent_fails_contradiction(tmp_path):
    cmd = _checks_with("review", "temporal-check")[0]
    from argus_skill.verticals.fiction_writing.state import apply_patch

    good, _ = apply_patch(None, {"patch_id": "p", "ops": [
        {"op": "set_meta", "set": {"world_clock": {"current_year": 2042}}},
        {"op": "add_character", "id": "c", "value": {"name": "林默", "birth_year": 2008, "age": 34}},
    ]})
    _write(tmp_path / "fiction" / "story_state.json", good)
    assert _run(cmd, tmp_path).returncode == 0

    bad, _ = apply_patch(None, {"patch_id": "p", "ops": [
        {"op": "set_meta", "set": {"world_clock": {"current_year": 2042}}},
        {"op": "add_character", "id": "c", "value": {"name": "林默", "birth_year": 2008, "age": 20}},
    ]})
    _write(tmp_path / "fiction" / "story_state.json", bad)
    assert _run(cmd, tmp_path).returncode != 0
