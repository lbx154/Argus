"""Vertical-aware builtin-skill seeding."""
from __future__ import annotations

import pytest

from argus.skills.builtins import (
    _RETIRED_BUILTIN_SEEDS,
    iter_builtin_skill_texts,
    iter_common_builtin_skill_texts,
    iter_vertical_skill_texts,
    remove_unmodified_inactive_context_skill_seeds,
    remove_unmodified_vertical_skill_seeds,
    seed_builtin_skills_for_vertical,
    seed_vertical_skills,
    vertical_skill_source_path,
)

MAINTENANCE_SKILLS = {
    "engineer/argus-maintenance-execution.md",
    "reviewer/argus-maintenance-review.md",
}

RETIRED_BUILTIN_SKILLS = {
    "engineer/experiment-audit.md",
    "engineer/nanochat-autoresearch-hands-on-trace.md",
    "engineer/nanochat-autoresearch-sota-optimization.md",
    "engineer/nanochat-pretrain-runner.md",
    "engineer/paper-claim-audit.md",
    "engineer/singularity-amlt-gpu-ops.md",
}

RETIRED_NANOCHAT_SKILLS = {
    "engineer/nanochat-autoresearch-hands-on-trace.md",
    "engineer/nanochat-autoresearch-sota-optimization.md",
    "engineer/nanochat-pretrain-runner.md",
}


def test_vertical_skill_source_path_rejects_injection() -> None:
    for bad in ("", "a/b", "..", ".hidden", "x\\y"):
        with pytest.raises(ValueError):
            vertical_skill_source_path(bad)


def test_retired_builtin_skills_are_not_packaged() -> None:
    packaged = {name for name, _text in iter_builtin_skill_texts()}

    assert packaged.isdisjoint(RETIRED_BUILTIN_SKILLS)


def test_minimal_coding_agent_skill_is_packaged() -> None:
    packaged = dict(iter_builtin_skill_texts())

    body = packaged["engineer/minimal-coding-agent.md"]
    assert "最少且足够的代码" in body
    assert "答不出来就不要添加" in body


def test_agent_team_lead_seed_is_retired() -> None:
    assert "agent-team-lead.md" not in dict(iter_common_builtin_skill_texts())
    assert "agent-team-lead.md" in _RETIRED_BUILTIN_SEEDS


def test_machine_specific_nanochat_playbook_seeds_stay_retired() -> None:
    # The H100 nanochat traces once lived in the flat builtin pool; operator
    # libraries seeded before they were retired still carry copies, so the
    # retirement names must survive the vertical's move to argus-verticals.
    assert RETIRED_NANOCHAT_SKILLS <= _RETIRED_BUILTIN_SEEDS


def test_atomic_write_accepts_concurrent_identical_winner(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus.skills.builtins as builtins

    destination = tmp_path / "shared.md"
    destination.write_text("same runtime seed\n", encoding="utf-8")
    monkeypatch.setattr(
        builtins.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(PermissionError("target busy")),
    )

    builtins._atomic_write_text(destination, "same runtime seed\n")

    assert destination.read_text(encoding="utf-8") == "same runtime seed\n"
    assert list(tmp_path.glob("shared.md.tmp.*")) == []


def test_remove_unmodified_vertical_seeds_preserves_learned_edits(tmp_path) -> None:
    seed_vertical_skills(tmp_path, "research")
    source_files = dict(iter_vertical_skill_texts("research"))
    markdown_files = [
        filename for filename in source_files if filename.endswith(".md")
    ]
    assert markdown_files
    seeded_files = list(source_files)
    assert len(seeded_files) >= 2
    modified = tmp_path / markdown_files[0]
    untouched_name = next(
        filename for filename in seeded_files if filename != markdown_files[0]
    )
    untouched = tmp_path / untouched_name
    modified.write_text(
        modified.read_text(encoding="utf-8") + "\nlearned project edit\n",
        encoding="utf-8",
    )

    removed = remove_unmodified_vertical_skill_seeds(tmp_path, "research")

    assert untouched_name in removed
    assert not untouched.exists()
    assert modified.exists()


def test_remove_inactive_vertical_seeds_prunes_inactive_but_preserves_edits_and_active(
    tmp_path,
) -> None:
    seed_vertical_skills(tmp_path, "argus_maintenance")
    seed_vertical_skills(tmp_path, "research")
    edited_name = "engineer/argus-maintenance-execution.md"
    edited = tmp_path / edited_name
    edited.write_text(
        edited.read_text(encoding="utf-8") + "\nproject-specific learning\n",
        encoding="utf-8",
    )

    removed = remove_unmodified_inactive_context_skill_seeds(
        tmp_path,
        "research",
    )

    assert set(removed) == MAINTENANCE_SKILLS - {edited_name}
    assert edited.exists()
    assert (
        tmp_path / "engineer" / "paper-framework-figure-studio.md"
    ).exists()


def test_seed_for_research_does_not_pull_another_verticals_skills(tmp_path) -> None:
    # A vertical that does not own the maintenance skills must see no trace of
    # them: not the real body (cross-vertical leakage) and no pointer stub either.
    seed_builtin_skills_for_vertical(tmp_path, "research", overwrite=True)
    for relative in MAINTENANCE_SKILLS:
        assert not (tmp_path / relative).exists(), relative
    assert (tmp_path / "engineer" / "paper-framework-figure-studio.md").is_file()
