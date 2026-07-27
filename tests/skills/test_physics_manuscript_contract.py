"""Mandatory manuscript-stage delivery contract for the physics vertical.

The physics vertical is five stages ending in a HARD ``manuscript`` stage: a
completed physics mission's deliverable is a discipline-agnostic research-paper
package, not a scope/model/execute/review log. There is no optional paper-target
mode, no marker file, and no env var — the verifier always checks and always
fails closed. These tests pin that, keep the physics core checklists, and guard
against physics-subfield hardcoding.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from argus_skill.verticals._base import load_vertical
from argus_skill.verticals.physics import manuscript as ms
from tests.skills._physics_paper_fixtures import write_complete_package


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #
def _write_complete_package(root: Path) -> None:
    """A project that satisfies the full manuscript contract (both the source
    layer and the LaTeX paper layer) — note: NO marker file is created (the
    contract is unconditional)."""
    write_complete_package(root)


@pytest.fixture()
def complete_package(tmp_path: Path) -> Path:
    _write_complete_package(tmp_path)
    return tmp_path


# --------------------------------------------------------------------------- #
# 1-2. five stages ending in a mandatory manuscript stage                     #
# --------------------------------------------------------------------------- #
def test_physics_stage_order_is_five_ending_in_manuscript() -> None:
    mod = load_vertical("physics")
    assert mod.STAGE_ORDER == ("scope", "model", "execute", "review", "manuscript")
    # Shell-check registries were retired; the vertical owns a reviewer-facing
    # checklist and leaves the verifier available as an agent-callable tool.
    assert not hasattr(mod, "STAGE_CHECKS")
    assert {item.id for item in mod.CHECKLIST_ITEMS["manuscript"]} >= {
        "manuscript.paper-package",
        "manuscript.paper-composition",
        "manuscript.review-audit",
    }


def test_only_physics_vertical_no_physics_paper() -> None:
    from argus_skill.skills import vertical_select

    assert "physics" in vertical_select.VERTICALS
    assert "physics_paper" not in vertical_select.VERTICALS
    physics_dir = Path(ms.__file__).resolve().parent
    assert not (physics_dir.parent / "physics_paper").exists()


# --------------------------------------------------------------------------- #
# 3-4. no marker / no inactive pass-through — the contract is unconditional    #
# --------------------------------------------------------------------------- #
def test_no_marker_or_activation_api() -> None:
    # the optional-mode API is gone
    assert not hasattr(ms, "paper_target_active")
    assert not hasattr(ms, "paper_target_requested")
    assert not hasattr(ms, "MARKER_REL")


def test_contract_checked_without_any_marker(tmp_path: Path) -> None:
    # a bare project (only pipeline state, NO PAPER_TARGET.json) still fails the
    # manuscript contract — there is no pass-through
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        '{"vertical":"physics"}', encoding="utf-8"
    )
    assert not (tmp_path / "research" / "PAPER_TARGET.json").exists()
    failures = ms.verify_manuscript_deliverables(tmp_path)
    assert failures  # non-empty: deliverables are required unconditionally
    assert ms.main(["check", "--project-root", str(tmp_path)]) == 1


# --------------------------------------------------------------------------- #
# 5-13. each deliverable is required; the complete package passes              #
# --------------------------------------------------------------------------- #
def test_complete_package_passes(complete_package: Path) -> None:
    assert ms.verify_manuscript_deliverables(complete_package) == []
    assert ms.main(["check", "--project-root", str(complete_package)]) == 0


def test_missing_manuscript_fails(complete_package: Path) -> None:
    (complete_package / "MANUSCRIPT.md").unlink()
    assert "MANUSCRIPT.md" in " ".join(ms.verify_manuscript_deliverables(complete_package))


def test_fewer_than_six_figures_fails(complete_package: Path) -> None:
    (complete_package / "figures" / "fig6_panel.png").unlink()
    fails = " ".join(ms.verify_manuscript_deliverables(complete_package))
    assert "figures/" in fails and ">= 6" in fails


def test_missing_figure_legends_fails(complete_package: Path) -> None:
    (complete_package / "FIGURE_LEGENDS.md").unlink()
    assert "FIGURE_LEGENDS.md" in " ".join(ms.verify_manuscript_deliverables(complete_package))


def test_fewer_than_eight_references_fails(complete_package: Path) -> None:
    (complete_package / "REFERENCES.bib").write_text(
        "\n".join(f"@article{{ref{i}}}" for i in range(3)), encoding="utf-8"
    )
    fails = " ".join(ms.verify_manuscript_deliverables(complete_package))
    assert "REFERENCES.bib/references.md" in fails and ">= 8" in fails


def test_missing_claims_ledger_fails(complete_package: Path) -> None:
    (complete_package / "CLAIMS.csv").write_text("claim_id,claim_text\nC1,x\n", encoding="utf-8")
    msg = " ".join(ms.verify_manuscript_deliverables(complete_package))
    assert "CLAIMS.csv" in msg and "missing:" in msg


def test_wrong_claims_header_fails_with_rename_hint(complete_package: Path) -> None:
    # the exact wrong schema an agent used: claim/evidence instead of the
    # required claim_text/evidence_type/evidence_pointer/reviewer_notes.
    (complete_package / "CLAIMS.csv").write_text(
        "claim_id,claim,claim_type,evidence,status,boundary\n"
        "C1,the model is consistent,theory,eq:1,supported,linear\n",
        encoding="utf-8",
    )
    fails = ms.verify_manuscript_deliverables(complete_package)
    assert fails and any("CLAIMS.csv header is wrong" in f for f in fails)
    msg = " ".join(fails)
    # synonyms are NOT accepted; the message must say to rename
    assert "synonyms are NOT accepted" in msg
    assert "rename 'claim' -> 'claim_text'" in msg
    assert "rename 'evidence'" in msg
    # expected + detected + missing are all shown
    assert ms.CLAIMS_HEADER in msg
    assert "detected: claim_id,claim,claim_type,evidence,status,boundary" in msg
    assert "reviewer_notes" in msg  # a missing column
    # and the CLI fails closed with the same guidance
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus_skill.verticals.physics.manuscript",
            "check",
            "--project-root",
            str(complete_package),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1
    assert "rename 'claim'" in r.stderr


def test_exact_claims_header_documented_in_contract() -> None:
    mod = load_vertical("physics")
    header = "claim_id,claim_text,claim_type,evidence_type,evidence_pointer,status,boundary,reviewer_notes"
    assert ms.CLAIMS_HEADER == header
    # The exact header must appear in the tool contract, role framing, and the
    # vertical-owned checklist consumed by the Reviewer.
    assert header in ms.manuscript_review_items()
    assert header in mod.role_banner("engineer")
    manuscript_items = " ".join(
        i.statement + " " + i.evidence_hint for i in mod.CHECKLIST_ITEMS["manuscript"]
    )
    assert header in manuscript_items


def test_paper_audit_heading_documented_in_contract() -> None:
    mod = load_vertical("physics")
    heading = ms.PAPER_AUDIT_HEADING
    assert heading == "Paper-Style Delivery Audit"
    # The exact REVIEW.md audit heading must appear in the active agent-facing surfaces.
    assert heading in ms.manuscript_review_items()
    assert heading in mod.role_banner("engineer")
    manuscript_items = " ".join(
        i.statement + " " + i.evidence_hint for i in mod.CHECKLIST_ITEMS["manuscript"]
    )
    assert heading in manuscript_items


def test_missing_reproducibility_fails(complete_package: Path) -> None:
    (complete_package / "REPRODUCIBILITY.md").unlink()
    assert "REPRODUCIBILITY.md" in " ".join(ms.verify_manuscript_deliverables(complete_package))


def test_missing_data_and_code_availability_fails(complete_package: Path) -> None:
    text = (complete_package / "MANUSCRIPT.md").read_text(encoding="utf-8")
    text = text.replace("## Data Availability\nx\n", "").replace("## Code Availability\nx\n", "")
    (complete_package / "MANUSCRIPT.md").write_text(text, encoding="utf-8")
    fails = " ".join(ms.verify_manuscript_deliverables(complete_package))
    assert "Data Availability" in fails and "Code Availability" in fails


def test_html_demo_absence_does_not_fail_manuscript_verifier(complete_package: Path) -> None:
    # The HTML demo is an OPTIONAL presentation layer: its absence must NOT fail
    # the manuscript gate (neither the source layer nor the aggregate contract).
    import shutil

    shutil.rmtree(complete_package / "HTML_DEMO")
    assert not (complete_package / "HTML_DEMO").exists()
    assert ms.verify_manuscript_deliverables(complete_package) == []
    assert ms.verify_all_deliverables(complete_package) == []
    assert ms.main(["check", "--project-root", str(complete_package)]) == 0
    # a PRESENTATION/ page is equally optional
    assert not (complete_package / "PRESENTATION").exists()
    assert ms.verify_all_deliverables(complete_package) == []


# --------------------------------------------------------------------------- #
# 14. banner: terminal deliverable is a manuscript package (no "thin" wording) #
# --------------------------------------------------------------------------- #
def test_role_banner_declares_manuscript_terminal_deliverable() -> None:
    mod = load_vertical("physics")
    for role in ("planner", "engineer", "reviewer"):
        banner = mod.role_banner(role)
        assert "research-paper package" in banner
        assert "manuscript" in banner.lower()
        assert "do not write a manuscript" not in banner
        assert "stay thin" not in banner.lower()


def test_manuscript_reviewer_checklist_audits_paper_and_no_overclaim() -> None:
    mod = load_vertical("physics")
    text = (
        " ".join(
            item.statement + " " + item.evidence_hint for item in mod.CHECKLIST_ITEMS["manuscript"]
        )
        + " "
        + mod.role_banner("reviewer")
    )
    for token in ("supported", "universal", "synthetic", "novelty", "provenance"):
        assert token in text.lower()


# --------------------------------------------------------------------------- #
# 15. the physics core scope/model/execute/review checklists are retained      #
# --------------------------------------------------------------------------- #
def test_physics_core_checklists_retained() -> None:
    mod = load_vertical("physics")
    items = mod.CHECKLIST_ITEMS
    assert [i.id for i in items["scope"]] == [
        "scope.faithful-goal",
        "scope.task-type-success",
        "scope.dynamic-route",
    ]
    assert {i.id for i in items["model"]} == {
        "model.variables-equations",
        "model.assumptions-bcic",
        "model.validation-target",
    }
    assert {i.id for i in items["review"]} >= {
        "review.no-system-drift",
        "review.units-bcic",
        "review.claim-status",
    }
    # review keeps its physics claim audit; the paper-package audit lives in manuscript
    assert "review.paper-target-contract" not in {i.id for i in items["review"]}
    assert items["manuscript"]  # non-empty terminal checklist


# --------------------------------------------------------------------------- #
# 16. discipline-agnostic — no physics-subfield hardcoding                     #
# --------------------------------------------------------------------------- #
def test_no_domain_specific_hardcoding() -> None:
    surface = " ".join(
        [
            " ".join(label for label, _ in ms.MANUSCRIPT_SECTIONS),
            " ".join(desc for _, desc in ms.REQUIRED_FILES),
            ms.manuscript_review_items(),
            " ".join(
                item.statement + " " + item.evidence_hint
                for item in load_vertical("physics").CHECKLIST_ITEMS["manuscript"]
            ),
            load_vertical("physics").role_banner("engineer"),
        ]
    ).lower()
    banned = (
        "pendulum",
        "wilberforce",
        "floquet",
        "graphene",
        "qubit",
        "electron",
        "galaxy",
        "black hole",
        "topological",
        "superconduct",
        "boson",
        "fermion",
        "neutrino",
        "lattice",
        "spin glass",
        "navier",
        "schrodinger",
        "ising",
        "kepler",
        "quark",
        "photon",
        "magnet",
        "ssh",
        "non-hermitian",
        "gbz",
        "zero-pi",
        "zero and pi",
    )
    hit = [w for w in banned if w in surface]
    assert not hit, f"manuscript contract must be discipline-agnostic; found: {hit}"


# --------------------------------------------------------------------------- #
# CLI through the real subprocess path stage_check uses                        #
# --------------------------------------------------------------------------- #
def test_cli_subprocess_fail_closed_then_pass(complete_package: Path) -> None:
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus_skill.verticals.physics.manuscript",
            "check",
            "--project-root",
            str(complete_package),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    (complete_package / "MANUSCRIPT.md").unlink()
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus_skill.verticals.physics.manuscript",
            "check",
            "--project-root",
            str(complete_package),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1
    assert "MANUSCRIPT.md" in r.stderr
