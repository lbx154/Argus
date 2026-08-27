from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.physics import downgrade, stages, tiers


def _seed(tmp_path: Path) -> Path:
    (tmp_path / ".argus").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps(
            {
                "current_stage": "execute",
                "stage_history": [
                    {"from_stage": "model", "to_stage": "execute", "direction": "advance"}
                    for _ in range(4)
                ],
                "rollback_history": [
                    {"from_stage": "execute", "to_stage": "model", "reason": "pivot"}
                    for _ in range(2)
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "ROUTE_CLOSURE_STATUS.json").write_text(
        json.dumps({"failed_round2_candidates": [{"id": "a"}, {"id": "b"}]}),
        encoding="utf-8",
    )
    return tmp_path


def test_tier_ladder_order_and_default(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_START_TIER", "B")
    assert tiers.TIER_ORDER == ("S", "A", "B", "C", "D")
    assert tiers.resolve_start_tier() == "B"
    assert tiers.next_lower_tier("B") == "C"
    assert tiers.next_lower_tier("D") == ""


def test_tier_d_is_negative_evidence_not_success_terminal() -> None:
    spec = tiers.tier_spec("D")
    assert spec is not None
    assert "Negative / null result" in spec.name
    assert "SUCCESS TERMINAL" not in spec.name


def test_downgrade_walks_b_to_d(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PHYSICS_START_TIER", "B")
    root = _seed(tmp_path)
    first = downgrade.evaluate_and_maybe_downgrade(root, now_iso="t")
    second = downgrade.evaluate_and_maybe_downgrade(root, now_iso="t")

    assert first is not None and first["to_tier"] == "C"
    assert second is not None and second["to_tier"] == "D"
    assert downgrade.evaluate_and_maybe_downgrade(root, now_iso="t") is None


def test_stage_checks_do_not_include_terminal_negative_gate() -> None:
    assert not hasattr(stages, "STAGE_CHECKS")
    banner = stages.role_banner("reviewer")
    assert "binary_rejection_terminal" not in banner
    assert "SUCCESS TERMINAL" not in banner


def test_role_banner_does_not_authorize_negative_result_as_success(tmp_path: Path) -> None:
    banner = stages.role_banner("reviewer", project_root=tmp_path)
    assert "SUCCESS TERMINAL" not in banner
    assert "manuscript_completion_authorized=true" not in banner
