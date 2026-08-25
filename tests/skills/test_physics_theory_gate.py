"""Tests for the Theory Capability gate (artifact verifier + repair loop)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from argus_skill.verticals.physics import stages
from argus_skill.verticals.physics.gates import theory as th


def _row(
    cid: str,
    *,
    applicable: bool = True,
    used: bool = True,
    kind: str = "generic",
    exec_level: str = "basic",
    evidence: bool = True,
    comparison: bool = True,
    impact: str = "",
    why: str = "",
    downgrade: str = "",
) -> dict:
    r = {c: "x" for c in th.REQUIRED_COLUMNS}
    r["capability_id"] = cid
    r["generic_or_domain_specific"] = "domain-specific" if kind == "domain" else "generic"
    r["is_applicable"] = "true" if applicable else "false"
    r["applicability_reason"] = "relevant here" if applicable else "n/a"
    r["used_by_argus"] = "true" if used else "false"
    r["execution_level_basic_or_advanced_or_missing"] = exec_level
    r["evidence_file"] = "research/MODEL.md" if evidence else ""
    r["comparison_to_prior_work"] = "extends ref [3]" if comparison else ""
    r["impact_if_missing"] = impact
    r["if_not_used_why"] = why
    r["claim_downgrade_if_missing"] = downgrade
    return r


def _write(root: Path, rows: list[dict], *, domain: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if domain:
        (root / th.DOMAIN_FILE).write_text(
            json.dumps(
                {
                    "primary_domain": "non-Hermitian systems",
                    "secondary_domains": ["Floquet"],
                    "confidence": 0.8,
                    "why_this_domain": "x",
                    "domain_specific_capabilities_loaded": ["y"],
                }
            ),
            encoding="utf-8",
        )
    with (root / th.ARTIFACT).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(th.REQUIRED_COLUMNS))
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _good_rows() -> list[dict]:
    return [_row("CAP-THBASE-01"), _row("CAP-THBASE-03"), _row("CAP-DOM-01", kind="domain")]


def _codes(root: Path) -> list[str]:
    return [f["failure_id"] for f in th.verify_theory_capability(root)]


def test_complete_theory_audit_passes(tmp_path: Path) -> None:
    _write(tmp_path, _good_rows())
    assert th.verify_theory_capability(tmp_path) == []
    passed, _ = th.run_gate(tmp_path)
    assert passed and (tmp_path / "research" / "THEORY_GATE_RESULT.json").is_file()


def test_missing_domain_classification_fails_th001(tmp_path: Path) -> None:
    _write(tmp_path, _good_rows(), domain=False)
    assert "TH-001" in _codes(tmp_path)


def test_missing_audit_fails_th000(tmp_path: Path) -> None:
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / th.DOMAIN_FILE).write_text('{"primary_domain":"x"}', encoding="utf-8")
    assert "TH-000" in _codes(tmp_path)


def test_no_domain_specific_capability_fails_th002(tmp_path: Path) -> None:
    _write(tmp_path, [_row("CAP-THBASE-01"), _row("CAP-THBASE-03")])  # all generic
    assert "TH-002" in _codes(tmp_path)


def test_incomplete_applicability_fails_th002(tmp_path: Path) -> None:
    rows = _good_rows()
    rows[0]["applicability_reason"] = ""
    _write(tmp_path, rows)
    assert "TH-002" in _codes(tmp_path)


def test_used_without_evidence_fails_th003(tmp_path: Path) -> None:
    rows = _good_rows()
    rows[0] = _row("CAP-THBASE-01", evidence=False)
    _write(tmp_path, rows)
    assert "TH-003" in _codes(tmp_path)


def test_applicable_missing_unjustified_fails_th004(tmp_path: Path) -> None:
    rows = _good_rows()
    rows.append(
        _row("CAP-MISS", used=False, exec_level="missing", impact="breaks stability claim", why="")
    )
    _write(tmp_path, rows)
    assert "TH-004" in _codes(tmp_path)


def test_used_without_comparison_fails_th005(tmp_path: Path) -> None:
    rows = _good_rows()
    rows[0] = _row("CAP-THBASE-01", comparison=False)
    _write(tmp_path, rows)
    assert "TH-005" in _codes(tmp_path)


def test_missing_without_downgrade_fails_th006(tmp_path: Path) -> None:
    rows = _good_rows()
    rows.append(
        _row(
            "CAP-MISS",
            used=False,
            exec_level="missing",
            impact="needed for claim C3",
            why="out of scope",
            downgrade="",
        )
    )
    _write(tmp_path, rows)
    assert "TH-006" in _codes(tmp_path)


def test_advisory_cli_and_repair_context(tmp_path: Path) -> None:
    _write(tmp_path, _good_rows(), domain=False)  # TH-001 blocker
    assert th.main(["check", "--project-root", str(tmp_path), "--advisory"]) == 0
    assert th.main(["check", "--project-root", str(tmp_path)]) == 1
    assert (tmp_path / "research" / "THEORY_GATE_REPAIR_TASKS.md").is_file()
    assert (tmp_path / "research" / "THEORY_GATE_STATE.json").is_file()


def test_theory_failures_do_not_become_model_prompt_work(tmp_path: Path) -> None:
    _write(tmp_path, _good_rows(), domain=False)
    th.run_gate(tmp_path)
    banner = stages.role_banner("engineer", project_root=tmp_path)
    assert "THEORY_GATE REPAIR REQUIRED" not in banner
    assert "TH-001" not in banner
    assert "Make equations, units, assumptions" in banner


def test_model_stage_focus_does_not_require_theory_gate() -> None:
    assert not hasattr(stages, "STAGE_CHECKS")
    banner = stages.role_banner("engineer")
    assert "gates.theory" not in banner
    assert "equations" in banner
    assert "follow the feedback" in banner


def test_theory_capabilities_still_load_via_registry() -> None:
    from argus_skill.skills.capability_registry import CapabilityRegistry

    reg = CapabilityRegistry(external_path=None)
    caps = reg.for_gate("theory")
    assert len(caps) >= 6 and all(c.source_layer == "base" for c in caps)
    assert all(c.pass_threshold for c in caps)  # publishable_standard mapped through
