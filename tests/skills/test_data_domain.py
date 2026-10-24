"""Tests for project-local DATA domains (``verticals/_data_domain``)."""
from __future__ import annotations

import json

import pytest

from argus.verticals import _data_domain as dd


def test_data_domain_summaries_expose_formal_purpose(tmp_path):
    path = dd.write_data_domain(
        tmp_path,
        "apple_mlx_inference",
        stages=["profile", "measure"],
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update({
        "status": "formal",
        "purpose": "Apple Silicon MLX/Metal deployment optimization",
    })
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert dd.data_domain_summaries(tmp_path) == {
        "apple_mlx_inference": (
            "status=formal; Apple Silicon MLX/Metal deployment optimization"
        )
    }


def test_selectable_summaries_include_local_candidate_and_prefer_learned_formal(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    learned = tmp_path / "learned"
    dd.write_data_domain(
        project,
        "robotics_eval",
        stages=["integrate", "evaluate"],
        status="candidate",
        purpose="project-local embodied evaluation",
    )
    assert dd.list_selectable_data_domain_summaries(project) == {
        "robotics_eval": "status=candidate; project-local embodied evaluation",
    }

    source = tmp_path / "source"
    dd.write_data_domain(
        source,
        "robotics_eval",
        stages=["integrate", "evaluate", "report"],
        status="candidate",
        purpose="verified embodied evaluation",
    )
    assert dd.promote_data_domain(source, learned, "robotics_eval")
    assert dd.list_selectable_data_domain_summaries(
        project,
        learned_root=learned,
    ) == {
        "robotics_eval": "status=formal; verified embodied evaluation",
    }


def test_name_validation_rejects_path_separators(tmp_path):
    assert not dd.is_valid_domain_name("../evil")
    assert not dd.is_valid_domain_name("a/b")
    assert not dd.is_valid_domain_name("Cap")        # uppercase not allowed
    assert dd.is_valid_domain_name("robo_sim2")
    with pytest.raises(ValueError):
        dd.write_data_domain(tmp_path, "../evil", stages=["a", "b"])


def test_create_only_unless_overwrite(tmp_path):
    dd.write_data_domain(tmp_path, "alpha", stages=["a", "b"])
    with pytest.raises(ValueError):
        dd.write_data_domain(tmp_path, "alpha", stages=["a", "b"])
    dd.write_data_domain(tmp_path, "alpha", stages=["x", "y"], overwrite=True)
    assert dd.load_data_domain("alpha", tmp_path).STAGE_ORDER == ["x", "y"]


def test_empty_stages_rejected(tmp_path):
    with pytest.raises(ValueError):
        dd.write_data_domain(tmp_path, "alpha", stages=[])


def test_candidate_becomes_reusable_after_first_verified_success(tmp_path):
    project = tmp_path / "project"
    learned = tmp_path / "global"
    dd.write_data_domain(
        project,
        "hardware_audit",
        stages=["inspect", "analyze", "report"],
        status="candidate",
        purpose="audit unfamiliar hardware deployments",
        require_independent_review=True,
    )

    domain = dd.load_data_domain("hardware_audit", project)
    assert domain.status == "candidate"
    assert domain.REQUIRE_INDEPENDENT_REVIEW is True
    assert dd.list_formal_data_domain_purposes(
        project,
        learned_root=learned,
    ) == {}

    assert dd.record_data_domain_failure(
        project,
        "hardware_audit",
        reason="missing device evidence",
    )
    assert dd.promote_data_domain(
        project,
        learned,
        "hardware_audit",
        review_reason="independent review passed",
    )

    formal = dd.load_data_domain("hardware_audit", project)
    assert formal.status == "formal"
    assert formal.REQUIRE_INDEPENDENT_REVIEW is True
    assert dd.list_formal_data_domain_purposes(
        tmp_path / "another-project",
        learned_root=learned,
    ) == {
        "hardware_audit": "audit unfamiliar hardware deployments"
    }
    another = tmp_path / "another-project"
    dd.write_data_domain(
        another,
        "hardware_audit",
        stages=["draft"],
        status="candidate",
        purpose="stale local candidate",
    )
    assert dd.materialize_learned_data_domain(
        learned,
        another,
        "hardware_audit",
    )
    assert dd.load_data_domain("hardware_audit", another).STAGE_ORDER == [
        "inspect",
        "analyze",
        "report",
    ]
    assert dd.load_data_domain(
        "hardware_audit",
        another,
    ).REQUIRE_INDEPENDENT_REVIEW is True
