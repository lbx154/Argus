"""Tests that the reviewer OUTPUT CONTRACT the skill/banner document actually
parses through the shared literary review contract, and that E3's grounding +
repair loop is wired into the engineer banner (the programmatic prompt).

This guards the root cause behind the E1 parse failures: a reviewer emitting the
documented shape must yield a valid revision plan; a shape that conflates
severity with blocking, invents a type, or uses ASCII inner quotes must not be
what we tell reviewers to produce.
"""
from __future__ import annotations

from argus_skill.verticals.fiction_writing.revise import fiction_revision_plan_from_text
from argus_skill.verticals.fiction_writing.stages import role_banner

# mirrors the corrected reviewer skill's example (verdict + findings envelope,
# severity decoupled from blocking, vocabulary types, 「」 inner quotes)
_CONFORMANT = """```json
{"verdict": "revise",
 "findings": [
   {"id": "F1", "type": "status", "severity": "critical", "blocking": true,
    "location": "第五回首段「贾敏走进潇湘馆」", "evidence": "已故的贾敏在场叙话",
    "suggested_action": "删去贾敏出场，或改为黛玉的幻景", "violated_constraint": "贾敏 status=dead"},
   {"id": "F2", "type": "temporal_consistency", "severity": "major", "blocking": true,
    "location": "黛玉年龄", "evidence": "黛玉「二十有五」", "suggested_action": "改为约十四岁"}
 ]}
```"""

_DONE = '{"verdict": "done", "findings": []}'


def test_conformant_reviewer_output_yields_blocking_plan():
    plan = fiction_revision_plan_from_text(_CONFORMANT)
    assert len(plan) == 2
    assert all(p["blocking"] for p in plan)
    assert {p["type"] for p in plan} == {"status", "temporal_consistency"}


def test_done_verdict_yields_empty_plan():
    assert fiction_revision_plan_from_text(_DONE) == []


def test_engineer_banner_wires_grounding_and_repair_loop():
    # E3 runtime wiring: the programmatic engineer prompt must ground state_patch
    # generation and route through the validate->repair loop, not just apply blindly.
    banner = role_banner("engineer")
    assert "apply_patch_with_repair" in banner
    assert "valid-id inventory" in banner


def test_reviewer_banner_states_the_contract():
    banner = role_banner("reviewer")
    assert "verdict" in banner and "findings" in banner
    assert "blocking(bool)" in banner  # severity and blocking kept distinct
