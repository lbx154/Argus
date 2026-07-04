"""Regression: stage_check must resolve project-local DATA-domain verticals.

A Manager can author a bespoke vertical as a project-local data domain
(``research/DOMAINS/<name>.json``) instead of a packaged ``argus_skill.verticals``
module. The runtime resolves those via the canonical ``load_vertical`` resolver
(supervisor/_core.py, loop.py, _runtime.py). ``stage_check`` was the LAST consumer
still using raw ``importlib.import_module`` and therefore crashed with
"unknown vertical" on every data-domain vertical the runtime resolved fine — so
the bounded acceptance gate could never run for a bespoke domain. This pins the
fix: stage_check now uses ``load_vertical`` too.

(This inconsistency was surfaced by a live self-hosted argus run whose engineer
correctly diagnosed and patched it — a genuine self-repair, landed here properly.)
"""
from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

from argus_skill.tools import stage_check
from argus_skill.verticals._base import load_vertical


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_load_vertical_resolves_project_local_data_domain(tmp_path: Path) -> None:
    # The resolver returns a duck-typed shim exposing the same tables a packaged
    # stages module does — which is exactly what stage_check reads off it.
    _write_json(
        tmp_path / "research" / "DOMAINS" / "python_tdd.json",
        {"name": "python_tdd", "stages": ["scope"]},
    )
    dom = load_vertical("python_tdd", project_root=tmp_path)
    assert list(dom.STAGE_ORDER) == ["scope"]
    assert dom.STAGE_CHECKS            # dict[stage -> checks]
    assert isinstance(dom.REVIEWER_CHECKLISTS, dict)


def test_stage_check_loads_data_domain_vertical_not_unknown(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_json(
        tmp_path / "research" / "DOMAINS" / "python_tdd.json",
        {"name": "python_tdd", "stages": ["scope"]},
    )
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {"vertical": "python_tdd", "current_stage": "scope"},
    )
    monkeypatch.setattr(
        sys, "argv",
        ["stage-check", "--project-root", str(tmp_path), "--bounded"],
    )
    status = stage_check.main()
    captured = capsys.readouterr()

    # The vertical RESOLVED: the stage banner (printed only after a successful
    # load) names it, and neither the old "unknown vertical" nor a load failure
    # appears. Return code is not asserted — structural gates may still flag the
    # bare domain; the point is that loading no longer crashes the gate.
    assert isinstance(status, int)
    assert "(vertical: python_tdd)" in captured.out
    assert "unknown vertical" not in captured.err
    assert "failed to load" not in captured.err


def test_stage_check_knowledge_curation_review_uses_curation_gate(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    quote = "  - Clip the importance ratio ASYMMETRICALLY (looser upper bound) — reduces variance vs\n  symmetric PPO clipping."
    _write_json(
        tmp_path / "research" / "DOMAINS" / "knowledge_curation.json",
        {"name": "knowledge_curation", "stages": ["review"], "completion_gate": "none"},
    )
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {"vertical": "knowledge_curation", "current_stage": "review"},
    )
    pipeline_sha = _sha(tmp_path / "research" / "PIPELINE_STATE.json")

    wiki = tmp_path / ".autors" / "learning" / "wiki"
    for rel in (
        "sources/papers",
        "sources/repos",
        "sources/runs",
        "sources/notes",
        "pages/techniques",
        "pages/conflicts",
        "pages/patterns",
        "data",
        "queries",
    ):
        (wiki / rel).mkdir(parents=True, exist_ok=True)
    (wiki / "data" / "schema.yaml").write_text("# schema\n", encoding="utf-8")
    (wiki / "query_pack.md").write_text("# pack\n", encoding="utf-8")
    (tmp_path / "material.md").write_text(quote + "\n", encoding="utf-8")
    (wiki / "sources" / "notes" / "material.md").write_text(
        "---\n"
        "id: material\n"
        "title: material\n"
        "mission_id: ''\n"
        "created_at: 2026-07-04\n"
        "tags: []\n"
        "---\n\n"
        f"{quote}\n",
        encoding="utf-8",
    )
    (wiki / "pages" / "techniques" / "grpo-practical-tricks.md").write_text(
        "---\n"
        "id: grpo-practical-tricks\n"
        "type: technique\n"
        "status: scratch\n"
        "title: GRPO Practical Tricks\n"
        "tags: [grpo]\n"
        "sources:\n"
        "- notes/material.md\n"
        "related_runs: []\n"
        "related_projects: []\n"
        "revisit_after: null\n"
        "created_at: 2026-07-04\n"
        "last_reviewed_at: 2026-07-04\n"
        "reviewer_note: ''\n"
        "---\n\n"
        "## Evidence\n\n"
        "- `material.md:L1-L2`:\n"
        "```text\n"
        f"{quote}\n"
        "```\n",
        encoding="utf-8",
    )
    (wiki / "queries" / "by-status.md").write_text(
        "# Cards by status\n\n## scratch\n- `technique/grpo-practical-tricks` -- GRPO Practical Tricks\n",
        encoding="utf-8",
    )

    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "grpo-practical-tricks.md").write_text(
        "---\n"
        "name: grpo-practical-tricks\n"
        "description: GRPO practical tricks\n"
        "category: learning\n"
        "provisional: true\n"
        "---\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(skills))

    _write_json(
        tmp_path / "research" / "REVIEW_CERTIFICATION.json",
        {
            "verdict": "review_gate_ready",
            "validate_wiki": "pass",
            "all_evidence_quote_checks_pass": True,
            "honest_null_ok": {
                "no_op": False,
                "reviewed_create_ops": 2,
                "review_repair_ops": 1,
                "fabricated_churn": False,
            },
            "pipeline_state_guard": {
                "sha256_before_round": pipeline_sha,
                "sha256_after_round": pipeline_sha,
                "byte_unchanged_during_review": True,
                "stage_fields_edited": False,
            },
            "provenance_recheck_table": [
                {
                    "locator": "material.md:L1-L2",
                    "quote": quote,
                    "pass": True,
                }
            ],
        },
    )

    monkeypatch.setattr(
        sys, "argv", ["stage-check", "--project-root", str(tmp_path), "--bounded"],
    )
    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 0
    assert "knowledge_curation_review (structural)" in out
    assert "paper_structural_minimums" not in out
    assert "reviewer_simulation" not in out
    assert "experiment_audit" not in out
