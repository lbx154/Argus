"""Vertical-aware builtin-skill seeding.

The skill-layering convention: ``argus_skill/builtin_skills/`` holds only
cross-vertical (general) skills; a vertical's own domain skills live under
``argus_skill/verticals/<v>/skills/{engineer,reviewer}/``. A moved domain skill
leaves a pointer STUB under ``builtin_skills/``; vertical-aware seeding copies
the REAL body into the agent workspace (overwriting that stub) only when the
active vertical is the one that owns it.

These tests pin that contract on the quant vertical (the first to adopt it).
"""
from __future__ import annotations

import hashlib

import pytest

import argus_skill.skills.builtins as builtins_module
from argus_skill.skills.builtins import (
    _validate_builtin,
    iter_builtin_skill_texts,
    iter_vertical_skill_texts,
    remove_unmodified_inactive_vertical_skill_seeds,
    remove_unmodified_vertical_skill_seeds,
    seed_builtin_skills_for_vertical,
    seed_vertical_skills,
    vertical_skill_source_path,
)
from argus_skill.skills.store import SkillStore

QUANT_SKILLS = {
    "engineer/quant-factor-loop.md",
    "engineer/model-selection-loop.md",
    "engineer/kline-chart.md",
    "reviewer/quant-factor-report-review.md",
}

MATH_SKILLS = {
    "manager/math-research-manager.md",
    "planner/math-research-planning.md",
    "engineer/math-research-execution.md",
    "reviewer/math-research-review.md",
    "scientist/math-research-distillation.md",
    "scientist/math-research-adaptation.md",
}


def test_iter_vertical_skill_texts_quant() -> None:
    got = {name for name, _ in iter_vertical_skill_texts("quant")}
    assert got == QUANT_SKILLS


def test_iter_vertical_skill_texts_math() -> None:
    got = {name for name, _ in iter_vertical_skill_texts("math")}
    assert got == MATH_SKILLS


def test_iter_vertical_skill_texts_unknown_or_skill_less_is_empty() -> None:
    assert list(iter_vertical_skill_texts("nope")) == []
    assert list(iter_vertical_skill_texts("software")) == []


def test_iter_vertical_skill_texts_research_visual_router() -> None:
    names = {name for name, _ in iter_vertical_skill_texts("research")}

    assert names == {
        "engineer/research-visualization-router.md",
        "engineer/research_visual_scripts/browser_render.py",
    }


def test_vertical_skill_source_path_rejects_injection() -> None:
    for bad in ("", "a/b", "..", ".hidden", "x\\y"):
        with pytest.raises(ValueError):
            vertical_skill_source_path(bad)


def test_vertical_owned_skills_are_not_also_flat_builtins() -> None:
    # The flat builtin pool is seeded into every runtime layer and every
    # project workspace, so anything left there is a matcher candidate for
    # every project forever. A skill a vertical owns must therefore live in
    # that vertical ONLY: a quant playbook or a B200 kernel trace must not
    # cost a maths or paper project summary tokens on every match.
    #
    # This used to be worked around with pointer stubs that stayed behind in
    # builtin_skills/. Stubs are candidates too — the seeding path already
    # skips them for the owning vertical, so they were pure dead weight for
    # everyone else. Deleting the skill from the flat pool is the fix; this
    # guard keeps it deleted.
    from argus_skill.skills.vertical_select import VERTICALS

    flat = {name for name, _text in iter_builtin_skill_texts()}
    leaked = {
        vertical: sorted({name for name, _t in iter_vertical_skill_texts(vertical)} & flat)
        for vertical in VERTICALS
    }
    assert {v: names for v, names in leaked.items() if names} == {}


def test_quant_skills_are_owned_by_the_quant_vertical(tmp_path) -> None:
    seed_builtin_skills_for_vertical(tmp_path, "quant", overwrite=True)
    for rel in QUANT_SKILLS:
        body = (tmp_path / rel).read_text(encoding="utf-8")
        assert "MOVED" not in body, f"pointer stub leaked into workspace for {rel}"


def test_all_builtins_valid_including_stubs() -> None:
    # Every bundled .md (stubs included) must parse with a name+description,
    # else the seeding pipeline's _validate_builtin would raise at runtime.
    for name, text in iter_builtin_skill_texts():
        if name.endswith(".md"):
            _validate_builtin(name, text)


def test_reference_corpora_are_not_enumerated_as_skills() -> None:
    names = {name for name, _text in iter_builtin_skill_texts()}

    assert not any("/references/" in f"/{name}" for name in names)


def test_seed_for_vertical_overwrites_stub_with_real_body(tmp_path) -> None:
    seed_builtin_skills_for_vertical(tmp_path, "quant", overwrite=True)
    for rel in QUANT_SKILLS:
        body = (tmp_path / rel).read_text(encoding="utf-8")
        assert "MOVED" not in body, f"stub leaked into workspace for {rel}"
    assert "strict quant-research referee" in (
        tmp_path / "reviewer" / "quant-factor-report-review.md"
    ).read_text(encoding="utf-8")
    assert "BacktestExecutor" in (
        tmp_path / "engineer" / "quant-factor-loop.md"
    ).read_text(encoding="utf-8")


def test_seed_for_vertical_keeps_cross_vertical_skills(tmp_path) -> None:
    # The vertical pass must NOT drop the general engineer/reviewer skills
    # (the iter_common_* helper skips subdirs; seed_for_vertical must not).
    seed_builtin_skills_for_vertical(tmp_path, "quant", overwrite=True)
    assert (tmp_path / "reviewer" / "experiment-plan-review.md").exists()
    assert (tmp_path / "engineer" / "argus-engineer-role.md").exists()


def test_seed_for_vertical_upgrades_known_unmodified_common_builtin(
    tmp_path,
    monkeypatch,
) -> None:
    relative = "engineer/research-results-analysis-and-figures.md"
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True)
    old = "known unmodified common builtin\n"
    destination.write_text(old, encoding="utf-8")
    monkeypatch.setitem(
        builtins_module._SAFE_BUILTIN_UPGRADE_DIGESTS,
        relative,
        {hashlib.sha256(old.encode()).hexdigest()},
    )

    seeded = seed_builtin_skills_for_vertical(tmp_path, "research")

    expected = dict(iter_builtin_skill_texts())[relative]
    assert seeded[relative] is True
    assert destination.read_text(encoding="utf-8") == expected


def test_seed_for_research_does_not_pull_quant_real_body(tmp_path) -> None:
    # A vertical that does not own the quant skills must see no trace of them:
    # not the real body (cross-vertical leakage) and no longer a pointer stub
    # either, which used to sit in every non-quant workspace as a dead matcher
    # candidate.
    seed_builtin_skills_for_vertical(tmp_path, "research", overwrite=True)
    for relative in QUANT_SKILLS:
        assert not (tmp_path / relative).exists(), relative
    assert (
        tmp_path / "engineer" / "research-visualization-router.md"
    ).is_file()
    assert (
        tmp_path / "engineer" / "research_visual_scripts" / "browser_render.py"
    ).is_file()


def test_seed_vertical_skills_writes_only_research_runtime_layer(
    tmp_path,
) -> None:
    written = seed_vertical_skills(tmp_path, "research")

    assert set(written) == {
        "engineer/research-visualization-router.md",
        "engineer/research_visual_scripts/browser_render.py",
    }


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


def test_remove_inactive_vertical_seeds_prunes_math_but_preserves_edits_and_active(
    tmp_path,
) -> None:
    seed_vertical_skills(tmp_path, "math")
    seed_vertical_skills(tmp_path, "research")
    edited_name = "engineer/math-research-execution.md"
    edited = tmp_path / edited_name
    edited.write_text(
        edited.read_text(encoding="utf-8") + "\nproject-specific learning\n",
        encoding="utf-8",
    )

    removed = remove_unmodified_inactive_vertical_skill_seeds(
        tmp_path,
        "research",
    )

    assert set(removed) == MATH_SKILLS - {edited_name}
    assert edited.exists()
    assert (
        tmp_path / "engineer" / "research-visualization-router.md"
    ).exists()


def test_remove_inactive_vertical_seeds_with_no_active_vertical_prunes_all(
    tmp_path,
) -> None:
    seed_vertical_skills(tmp_path, "math")

    removed = remove_unmodified_inactive_vertical_skill_seeds(tmp_path, None)

    assert set(removed) == MATH_SKILLS
    assert not any((tmp_path / filename).exists() for filename in MATH_SKILLS)


def test_vertical_seed_refresh_preserves_identified_shared_evolution(
    tmp_path,
) -> None:
    seed_vertical_skills(tmp_path, "research")
    store = SkillStore(tmp_path)
    skill = next(
        store.load(str(row["path"]))
        for row in store.list_summaries()
        if row["name"] == "Research Visualization Router"
    )
    skill.skill_id = "shared-evolved-id"
    skill.content += "\nlearned shared mechanism\n"
    store.save(skill)

    seed_vertical_skills(
        tmp_path,
        "research",
        overwrite_unidentified=True,
    )

    preserved = store.load(skill.path)
    assert preserved.skill_id == "shared-evolved-id"
    assert "learned shared mechanism" in preserved.content
    assert not (tmp_path / "engineer" / "argus-engineer-role.md").exists()
