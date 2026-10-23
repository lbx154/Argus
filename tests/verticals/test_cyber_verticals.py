"""Built-in cyber verticals: ``red_team`` and ``blue_team`` contracts and gates.

Phase 1 of the cyber-verticals pipeline: the engagement declaration gate's
accept/reject paths, the checklist/registry contracts, and blue-team triage of
a sample ``FINDINGS.json``.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from argus.skills.builtins import (
    seed_builtin_skills_for_vertical,
    vertical_skill_parents,
)
from argus.skills.vertical_select import (
    VERTICAL_PURPOSES,
    VERTICALS,
    available_vertical_purposes,
    available_verticals,
    require_vertical,
)
from argus.verticals import builtin_verticals
from argus.verticals._base import load_vertical, load_vertical_contract
from argus.verticals.blue_team.stages import HARDENING_SUMMARY_FILE
from argus.verticals.blue_team.triage import triage_findings
from argus.verticals.red_team.engagement import ENGAGEMENT_FILE, engagement_issues
from argus.verticals.red_team.exploit_evidence import sha256_file
from argus.verticals.red_team.findings import FINDINGS_FILE, findings_issues

TODAY = date.today()


def _write_engagement(project: Path, **overrides) -> None:
    declaration = {
        "operator": "Operator Y",
        "window": {
            "start": (TODAY - timedelta(days=1)).isoformat(),
            "end": (TODAY + timedelta(days=1)).isoformat(),
        },
        "targets": [
            {
                "kind": "host",
                "ref": "localhost:8000",
                "authorization": "self-owned scratch service",
            }
        ],
        "methods": ["source_audit", "local_reproduction"],
        "impact": {"destructive": False, "dos": False, "data_modification": False},
    }
    declaration.update(overrides)
    (project / ENGAGEMENT_FILE).write_text(
        json.dumps(declaration, indent=2), encoding="utf-8"
    )


def _write_findings(project: Path, findings: list[dict]) -> None:
    (project / FINDINGS_FILE).write_text(
        json.dumps({"findings": findings}, indent=2), encoding="utf-8"
    )


def _evidence(project: Path, name: str, content: bytes) -> dict:
    path = project / "evidence" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(content)
    return {"path": f"evidence/{name}", "sha256": sha256_file(path)}


# --- registry inventory ------------------------------------------------------


def test_cyber_verticals_are_registered_built_ins() -> None:
    assert "red_team" in VERTICALS
    assert "blue_team" in VERTICALS
    assert builtin_verticals() == VERTICALS
    assert require_vertical("red_team") == "red_team"
    assert require_vertical("blue_team") == "blue_team"
    assert set(VERTICAL_PURPOSES) == set(VERTICALS)
    purposes = available_vertical_purposes()
    assert "engagement-gated" in purposes["red_team"]
    assert "triage findings" in purposes["blue_team"]
    assert ("red_team" in available_verticals()) is True
    assert "blue_team" in available_verticals()


def test_red_team_contract() -> None:
    contract = load_vertical_contract("red_team")
    assert contract.stage_order == ("authorize", "attack", "report")
    assert contract.workflow_mode == "staged"
    assert contract.requires_independent_review is True
    assert contract.completion_gate == "none"
    assert contract.completion_contract_version == 1
    assert contract.mission_kind == "software"
    assert contract.primary_deliverables("report") == (FINDINGS_FILE,)
    provider = load_vertical("red_team")
    assert frozenset(provider.PROTECTED_ITEM_IDS) == {
        "authorize.declaration-valid",
        "attack.evidence-per-finding",
        "report.reproduced",
    }
    for stage, items in provider.CHECKLIST_ITEMS.items():
        assert items, f"empty checklist for {stage}"
    assert "SECURITY_ENGAGEMENT.json" in contract.banner("engineer")


def test_blue_team_contract() -> None:
    contract = load_vertical_contract("blue_team")
    assert contract.stage_order == ("triage", "remediate", "verify")
    assert contract.workflow_mode == "staged"
    assert contract.requires_independent_review is True
    assert contract.completion_gate == "none"
    assert contract.completion_contract_version == 1
    assert contract.mission_kind == "software"
    assert contract.primary_deliverables("verify") == (HARDENING_SUMMARY_FILE,)
    provider = load_vertical("blue_team")
    assert frozenset(provider.PROTECTED_ITEM_IDS) == {
        "triage.findings-triaged",
        "remediate.builds",
        "remediate.tests-executed",
        "remediate.fail-before-pass-after",
        "verify.poc-blocked",
        "verify.detection-fires",
        "verify.hardening-summary",
    }
    # The remediation floor names software's semantics, namespaced here.
    assert "compiles / type-checks" in provider.CHECKLIST_ITEMS["remediate"][0].statement
    assert "fail" in provider.CHECKLIST_ITEMS["remediate"][2].id


# --- the engagement declaration gate -----------------------------------------


def test_engagement_gate_accepts_a_valid_declaration(tmp_path: Path) -> None:
    _write_engagement(tmp_path)
    assert engagement_issues(tmp_path, today=TODAY) == ()
    # A mission target covered by the declared entry is accepted.
    assert engagement_issues(tmp_path, mission_targets=["localhost:8000"], today=TODAY) == ()


def test_engagement_gate_rejects_a_missing_or_broken_declaration(tmp_path: Path) -> None:
    issues = engagement_issues(tmp_path, today=TODAY)
    assert issues and "missing" in issues[0]
    (tmp_path / ENGAGEMENT_FILE).write_text("{not json", encoding="utf-8")
    assert any("not valid JSON" in issue for issue in engagement_issues(tmp_path, today=TODAY))
    (tmp_path / ENGAGEMENT_FILE).write_text("[]", encoding="utf-8")
    assert any("JSON object" in issue for issue in engagement_issues(tmp_path, today=TODAY))


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"operator": "  "}, "operator"),
        ({"window": {"start": "2026-10-24", "end": "2026-10-24"}}, "not current"),
        ({"window": {"start": "2026-10-26", "end": "2026-10-27"}}, "not current"),
        ({"window": {"start": "soon", "end": "2026-10-27"}}, "ISO date"),
        ({"window": {"start": "2026-10-27", "end": "2026-10-24"}}, "ends before"),
        ({"targets": [{"kind": "vuln", "ref": "x", "authorization": "y"}]}, "kind"),
        ({"targets": []}, "non-empty list"),
        (
            {"targets": [{"kind": "host", "ref": "localhost:8000", "authorization": ""}]},
            "authorization",
        ),
        ({"methods": ["source_audit", "payload_swap"]}, "unknown method category"),
        ({"methods": []}, "non-empty list"),
        ({"methods": ["dos_proof"]}, "dos"),
        ({"methods": ["destructive_proof", "data_modification_proof"]}, "destructive"),
        ({"impact": {"dos": False, "data_modification": False}}, "destructive is missing"),
        ({"impact": {"destructive": "no", "dos": False, "data_modification": False}}, "boolean"),
    ],
)
def test_engagement_gate_reject_paths(
    tmp_path: Path, overrides: dict, fragment: str
) -> None:
    _write_engagement(tmp_path, **overrides)
    issues = engagement_issues(tmp_path, today=TODAY)
    assert issues, f"gate accepted an invalid declaration: {overrides}"
    assert any(fragment in issue for issue in issues), issues


def test_engagement_gate_requires_every_mission_target_to_be_declared(
    tmp_path: Path,
) -> None:
    _write_engagement(tmp_path)
    issues = engagement_issues(
        tmp_path, mission_targets=["localhost:8000", "localhost:9999"], today=TODAY
    )
    assert any("localhost:9999" in issue and "not covered" in issue for issue in issues)


def test_engagement_gate_accepts_explicitly_enabled_impact(tmp_path: Path) -> None:
    _write_engagement(
        tmp_path,
        methods=["local_reproduction", "dos_proof"],
        impact={"destructive": False, "dos": True, "data_modification": False},
    )
    assert engagement_issues(tmp_path, today=TODAY) == ()


# --- the FINDINGS.json deliverable gate --------------------------------------


def test_findings_gate_accepts_a_bound_reproduction(tmp_path: Path) -> None:
    poc = _evidence(tmp_path, "R-1-poc.py", b"print('canary marker')\n")
    output = _evidence(tmp_path, "R-1-output.txt", b"canary marker\n")
    _write_findings(
        tmp_path,
        [
            {
                "id": "R-1",
                "target": "localhost:8000",
                "severity": "P2",
                "title": "Path traversal in download endpoint",
                "evidence": [poc, output],
                "reproduction": "python evidence/R-1-poc.py",
            }
        ],
    )
    assert findings_issues(tmp_path) == ()


def test_findings_gate_accepts_an_honest_null_result(tmp_path: Path) -> None:
    _write_findings(tmp_path, [])
    assert findings_issues(tmp_path) == ()


def test_findings_gate_reject_paths(tmp_path: Path) -> None:
    # Missing file.
    assert findings_issues(tmp_path) and "missing" in findings_issues(tmp_path)[0]
    # Missing reproduction command.
    poc = _evidence(tmp_path, "R-1-poc.py", b"print('canary')\n")
    _write_findings(
        tmp_path,
        [
            {
                "id": "R-1",
                "target": "localhost:8000",
                "severity": "P2",
                "title": "T",
                "evidence": [poc],
            }
        ],
    )
    assert any("reproduction" in issue for issue in findings_issues(tmp_path))
    # Rewritten evidence no longer matches its recorded hash.
    (tmp_path / "evidence" / "R-1-poc.py").write_bytes(b"tampered\n")
    issues = findings_issues(tmp_path)
    assert any("reproduction" in issue for issue in issues)  # still no command
    # ... with the command present, the hash mismatch alone blocks the stage.
    _write_findings(
        tmp_path,
        [
            {
                "id": "R-1",
                "target": "localhost:8000",
                "severity": "P2",
                "title": "T",
                "evidence": [poc],
                "reproduction": "python evidence/R-1-poc.py",
            }
        ],
    )
    assert any("sha256" in issue for issue in findings_issues(tmp_path))
    # Evidence outside the project is rejected.
    _write_findings(
        tmp_path,
        [
            {
                "id": "R-2",
                "target": "localhost:8000",
                "severity": "P1",
                "title": "T",
                "evidence": [{"path": "../../etc/passwd", "sha256": "0" * 64}],
                "reproduction": "cat /etc/passwd",
            }
        ],
    )
    assert any("escapes" in issue for issue in findings_issues(tmp_path))


def test_findings_gate_rejects_a_bad_severity_or_duplicate_id(tmp_path: Path) -> None:
    poc = _evidence(tmp_path, "R-1-poc.py", b"x = 1\n")
    finding = {
        "id": "R-1",
        "target": "localhost:8000",
        "severity": "P9",
        "title": "T",
        "evidence": [poc],
        "reproduction": "python evidence/R-1-poc.py",
    }
    _write_findings(tmp_path, [finding, dict(finding)])
    issues = findings_issues(tmp_path)
    assert any("severity" in issue for issue in issues)
    assert any("duplicate finding id" in issue for issue in issues)


# --- the deterministic stage gates ------------------------------------------


def test_red_team_stage_gates(tmp_path: Path) -> None:
    contract = load_vertical_contract("red_team")
    blocked = contract.completion_issues("authorize", tmp_path)
    assert blocked and ENGAGEMENT_FILE in blocked[0]
    _write_engagement(tmp_path)
    assert contract.completion_issues("authorize", tmp_path) == ()
    # attack has no deterministic gate; report needs the deliverable.
    assert contract.completion_issues("attack", tmp_path) == ()
    assert contract.completion_issues("report", tmp_path) and "missing" in contract.completion_issues("report", tmp_path)[0]
    _write_findings(tmp_path, [])
    assert contract.completion_issues("report", tmp_path) == ()
    assert contract.completion_issues("sidequest", tmp_path) == (
        "unknown red_team stage 'sidequest'",
    )


def test_blue_team_stage_gates(tmp_path: Path) -> None:
    contract = load_vertical_contract("blue_team")
    blocked = contract.completion_issues("triage", tmp_path)
    assert blocked and FINDINGS_FILE in blocked[0]
    _write_findings(tmp_path, [])
    assert contract.completion_issues("triage", tmp_path) == ()
    assert contract.completion_issues("remediate", tmp_path) == ()
    assert contract.completion_issues("verify", tmp_path) and "HARDENING_SUMMARY.md" in contract.completion_issues("verify", tmp_path)[0]
    (tmp_path / HARDENING_SUMMARY_FILE).write_text("# Hardening\n\nBefore/after.\n", encoding="utf-8")
    assert contract.completion_issues("verify", tmp_path) == ()
    assert contract.completion_issues("retire", tmp_path) == (
        "unknown blue_team stage 'retire'",
    )


# --- blue-team triage of a sample FINDINGS.json ------------------------------


def _sample_findings(tmp_path: Path) -> list[dict]:
    return [
        {
            "id": "R-3",
            "target": "localhost:8000",
            "severity": "P3",
            "title": "Verbose error page",
            "evidence": [_evidence(tmp_path, "R-3-poc.py", b"3\n")],
            "reproduction": "python evidence/R-3-poc.py",
        },
        {
            "id": "R-1",
            "target": "localhost:8000",
            "severity": "P2",
            "title": "Path traversal in download endpoint",
            "evidence": [_evidence(tmp_path, "R-1-poc.py", b"1\n")],
            "reproduction": "python evidence/R-1-poc.py",
        },
        # Same target+title as R-1 but lower severity: a collapsed duplicate.
        {
            "id": "R-9",
            "target": "localhost:8000",
            "severity": "P4",
            "title": "Path Traversal In Download Endpoint",
            "evidence": [_evidence(tmp_path, "R-9-poc.py", b"9\n")],
            "reproduction": "python evidence/R-9-poc.py",
        },
        # Duplicate id of R-1: dropped outright.
        {
            "id": "R-1",
            "target": "localhost:8000",
            "severity": "P0",
            "title": "Different title, same id",
            "evidence": [_evidence(tmp_path, "R-1b-poc.py", b"1b\n")],
            "reproduction": "python evidence/R-1b-poc.py",
        },
        {
            "id": "R-2",
            "target": "localhost:8000",
            "severity": "P1",
            "title": "Command injection in status endpoint",
            # No evidence artifacts at all: non-reproducible.
            "evidence": [],
            "reproduction": "",
        },
    ]


def test_blue_triage_ranks_dedupes_and_drops_non_reproducible(tmp_path: Path) -> None:
    report = triage_findings(_sample_findings(tmp_path), tmp_path)
    assert [finding["id"] for finding in report.actionable] == ["R-1", "R-3"]
    assert [finding["severity"] for finding in report.actionable] == ["P2", "P3"]
    assert [finding["id"] for finding, _ in report.unverified] == ["R-2"]
    reason = report.unverified[0][1]
    assert "reproduction" in reason or "evidence" in reason
    assert {finding["id"] for finding in report.dropped_duplicates} == {"R-9", "R-1"}
    # Nothing unverified or duplicated reaches remediation.
    assert all(
        finding["id"] not in {"R-2", "R-9"} for finding in report.actionable
    )


def test_blue_triage_uses_the_same_reproducibility_bar_as_red_report(
    tmp_path: Path,
) -> None:
    findings = _sample_findings(tmp_path)
    # Tamper with R-3's artifact: the hash binding breaks.
    (tmp_path / "evidence" / "R-3-poc.py").write_bytes(b"tampered\n")
    report = triage_findings(findings, tmp_path)
    assert [finding["id"] for finding, _ in report.unverified] == ["R-3", "R-2"]
    assert [finding["id"] for finding in report.actionable] == ["R-1"]


# --- skill inheritance: red_team seeds before blue_team -----------------------


def test_blue_team_seeds_red_team_skills_before_its_own(tmp_path: Path) -> None:
    assert vertical_skill_parents("blue_team") == ("red_team",)
    assert vertical_skill_parents("red_team") == ()
    written = seed_builtin_skills_for_vertical(
        tmp_path, "blue_team", overwrite=True
    )
    red_team_skills = {
        "engineer/authorized-attack-execution.md",
        "planner/authorized-engagement-planning.md",
        "manager/red-team-mission-management.md",
        "reviewer/red-team-review.md",
    }
    blue_team_skills = {
        "engineer/defense-remediation-implementation.md",
        "planner/defense-triage-planning.md",
        "manager/blue-team-mission-management.md",
        "reviewer/blue-team-review.md",
    }
    assert red_team_skills <= set(written)
    assert blue_team_skills <= set(written)

    # Seeding red_team alone does not pull in blue_team's skills.
    other = tmp_path / "red-only"
    red_written = seed_builtin_skills_for_vertical(other, "red_team", overwrite=True)
    assert red_team_skills <= set(red_written)
    assert not (set(red_written) & blue_team_skills)
