from __future__ import annotations

import json
from pathlib import Path

from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals._base import (
    load_vertical,
    vertical_stage_completion_issues,
)
from argus_skill.verticals.research.argument_organization import (
    ARGUMENT_ORGANIZATION_PATH,
    argument_organization_issues,
)


def _target(root: Path, level: str = "publishable") -> None:
    persist_vertical(root, "research", research_target_level=level)


def _rich(label: str) -> str:
    return (
        f"{label} is extracted from the accepted paper and explains a concrete "
        "organizational role that will be adapted to local evidence."
    )


def _payload(root: Path) -> dict:
    for slug in ("accepted-one", "accepted-two"):
        directory = root / "paper" / "style_ref" / "exemplars" / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "paper.pdf").write_bytes(b"%PDF exemplar\n")
        (directory / "paper.txt").write_text(
            "Extracted full paper text for organization study.\n",
            encoding="utf-8",
        )
    checkout = root / "paper" / "style_ref" / "code" / "accepted-one"
    checkout.mkdir(parents=True)
    (checkout / "train.py").write_text("def train(): pass\n", encoding="utf-8")
    (checkout / "evaluate.py").write_text("def evaluate(): pass\n", encoding="utf-8")

    argument_map = {
        field: _rich(field)
        for field in (
            "problem_setup",
            "gap_move",
            "organizing_insight",
            "contribution_sequence",
            "method_decomposition",
            "evidence_sequence",
            "figure1_role",
            "limitations_role",
            "conclusion_move",
        )
    }
    return {
        "schema_version": 1,
        "research_target_level": "publishable",
        "no_prose_copy_attestation": True,
        "reproduction_not_required_attestation": True,
        "code_requirement": "required",
        "exemplars": [
            {
                "slug": "accepted-one",
                "title": "Accepted Same-Area Method",
                "venue": "ICLR 2026",
                "official_acceptance_url": "https://iclr.cc/virtual/2026/poster/1",
                "why_same_area_and_shape": _rich("same area and contribution shape"),
                "local_pdf": "paper/style_ref/exemplars/accepted-one/paper.pdf",
                "text_extract": "paper/style_ref/exemplars/accepted-one/paper.txt",
                "argument_map": argument_map,
                "official_code": {
                    "availability": "available",
                    "repo_url": "https://github.com/example/accepted-one",
                    "revision": "abcdef1234567890",
                    "local_checkout": "paper/style_ref/code/accepted-one",
                    "files_inspected": [
                        "paper/style_ref/code/accepted-one/train.py",
                        "paper/style_ref/code/accepted-one/evaluate.py",
                    ],
                    "entry_points": _rich("training and evaluation entry points"),
                    "module_map": _rich("model, data, trainer, and evaluator modules"),
                    "config_and_evaluation_flow": _rich(
                        "configuration to training to evaluation flow"
                    ),
                    "reusable_organization_lessons": _rich(
                        "code organization lessons"
                    ),
                },
            },
            {
                "slug": "accepted-two",
                "title": "Accepted Same-Area Analysis",
                "venue": "ICLR 2026",
                "official_acceptance_url": "https://iclr.cc/virtual/2026/poster/2",
                "why_same_area_and_shape": _rich("same area analysis shape"),
                "local_pdf": "paper/style_ref/exemplars/accepted-two/paper.pdf",
                "text_extract": "paper/style_ref/exemplars/accepted-two/paper.txt",
                "argument_map": argument_map,
                "official_code": {
                    "availability": "not_released",
                    "reason": (
                        "The official acceptance page and paper do not link a "
                        "released repository as of the recorded access date."
                    ),
                },
            },
        ],
        "transfer_plan": {
            field: _rich(field)
            for field in (
                "local_argument_arc",
                "section_roles",
                "method_narrative",
                "experiment_narrative",
                "figure1_job",
                "code_structure_lessons",
                "evidence_based_deviations",
            )
        },
    }


def _write(root: Path, payload: dict) -> None:
    path = root / ARGUMENT_ORGANIZATION_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_exploratory_work_does_not_require_argument_map(tmp_path: Path) -> None:
    _target(tmp_path, "exploratory")

    assert argument_organization_issues(tmp_path) == ()


def test_external_state_root_blocks_plan_without_argument_map(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "workdir"
    state_root = tmp_path / "state" / "session"
    workdir.mkdir()
    _target(state_root)

    issues = vertical_stage_completion_issues(
        load_vertical("research", project_root=state_root),
        stage="plan",
        project_root=workdir,
        state_root=state_root,
    )

    assert any("missing paper/style_ref/ARGUMENT_ORGANIZATION.json" in issue for issue in issues)


def test_valid_paper_and_code_organization_map_passes(tmp_path: Path) -> None:
    _target(tmp_path)
    _write(tmp_path, _payload(tmp_path))

    assert argument_organization_issues(tmp_path) == ()


def test_publishable_paper_still_requires_two_accepted_exemplars(
    tmp_path: Path,
) -> None:
    _target(tmp_path)
    payload = _payload(tmp_path)
    payload["exemplars"] = payload["exemplars"][:1]
    _write(tmp_path, payload)

    issues = argument_organization_issues(tmp_path)

    assert any("at least 2 accepted same-area papers" in issue for issue in issues)


def test_available_code_requires_real_checkout_and_inspected_files(
    tmp_path: Path,
) -> None:
    _target(tmp_path)
    payload = _payload(tmp_path)
    code = payload["exemplars"][0]["official_code"]
    code["local_checkout"] = "paper/style_ref/code/missing"
    code["files_inspected"] = [
        "paper/style_ref/code/accepted-one/train.py",
        "../outside.py",
    ]
    _write(tmp_path, payload)

    issues = argument_organization_issues(tmp_path)

    assert any("local checkout does not exist" in issue for issue in issues)
    assert any("local file escapes project root" in issue for issue in issues)


def test_prose_copy_or_missing_argument_roles_are_rejected(tmp_path: Path) -> None:
    _target(tmp_path)
    payload = _payload(tmp_path)
    payload["no_prose_copy_attestation"] = False
    payload["exemplars"][0]["argument_map"]["organizing_insight"] = "TODO"
    _write(tmp_path, payload)

    issues = argument_organization_issues(tmp_path)

    assert "no_prose_copy_attestation must be true" in issues
    assert any("argument_map.organizing_insight" in issue for issue in issues)
