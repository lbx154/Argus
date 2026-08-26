"""Vertical-aware builtin-skill seeding."""
from __future__ import annotations

import hashlib
import json

import pytest

from argus_skill.skills.builtins import (
    _RETIRED_BUILTIN_SEED_HASHES,
    _validate_builtin,
    iter_builtin_skill_texts,
    iter_common_builtin_skill_texts,
    iter_vertical_skill_texts,
    remove_unmodified_inactive_context_skill_seeds,
    remove_unmodified_vertical_skill_seeds,
    retire_orphaned_builtin_seeds,
    seed_builtin_skills,
    seed_builtin_skills_for_vertical,
    seed_vertical_skills,
    vertical_skill_source_path,
)

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

RESEARCH_BASE_SKILLS = {
    "engineer/figure_spec_scripts/figure_renderer.py",
    "engineer/research-grind.md",
    "engineer/suspect-the-setup.md",
    "engineer/figure_spec_scripts/paper_chart_style.py",
    "engineer/paper-framework-figure-studio.md",
    "engineer/research-visualization-router.md",
    "engineer/research_visual_scripts/browser_render.py",
}
_RESEARCH_MOVE_MARKER = json.loads(
    (
        vertical_skill_source_path("research")
        / ".moved-from-global.json"
    ).read_text(encoding="utf-8")
)
RESEARCH_MOVED_SKILLS = set(
    _RESEARCH_MOVE_MARKER.get("paths", ())
    if isinstance(_RESEARCH_MOVE_MARKER, dict)
    else _RESEARCH_MOVE_MARKER
)
RESEARCH_SKILLS = RESEARCH_BASE_SKILLS | RESEARCH_MOVED_SKILLS


def test_iter_vertical_skill_texts_quant() -> None:
    got = {name for name, _ in iter_vertical_skill_texts("quant")}
    assert got == QUANT_SKILLS


def test_iter_vertical_skill_texts_math() -> None:
    got = {name for name, _ in iter_vertical_skill_texts("math")}
    assert got == MATH_SKILLS


def test_iter_vertical_skill_texts_unknown_or_skill_less_is_empty() -> None:
    assert list(iter_vertical_skill_texts("nope")) == []
    software = dict(iter_vertical_skill_texts("software"))
    assert set(software) == {
        "engineer/software-change-implementation.md",
        "manager/software-project-grounding.md",
        "planner/software-project-grounding.md",
        "reviewer/software-change-review.md",
    }


def test_iter_vertical_skill_texts_research_visual_router() -> None:
    names = {name for name, _ in iter_vertical_skill_texts("research")}

    assert names == RESEARCH_SKILLS


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


def test_retired_builtin_skills_are_not_packaged() -> None:
    packaged = {name for name, _text in iter_builtin_skill_texts()}

    assert packaged.isdisjoint(RETIRED_BUILTIN_SKILLS)


def test_minimal_coding_agent_skill_is_packaged() -> None:
    packaged = dict(iter_builtin_skill_texts())

    body = packaged["engineer/minimal-coding-agent.md"]
    assert "最少且足够的代码" in body
    assert "答不出来就不要添加" in body


def test_agent_team_lead_is_a_common_builtin() -> None:
    common = dict(iter_common_builtin_skill_texts())
    packaged = dict(iter_builtin_skill_texts())

    assert "agent-team-lead.md" in common
    assert "engineer/agent-team-lead.md" not in packaged
    assert "Every role may discover this Skill" in common["agent-team-lead.md"]


def test_machine_specific_nanochat_playbooks_are_retired() -> None:
    packaged = {name for name, _text in iter_vertical_skill_texts("nanochat")}

    assert packaged == set()
    assert RETIRED_NANOCHAT_SKILLS <= _RETIRED_BUILTIN_SEED_HASHES.keys()


def test_retire_orphaned_builtin_seeds_archives_edited_copies(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus_skill.skills.builtins as builtins

    unchanged_body = b"retired seed\n"
    edited_body = unchanged_body + b"operator edit\n"
    retired = {
        "engineer/unchanged.md": hashlib.sha256(unchanged_body).hexdigest(),
        "engineer/edited.md": hashlib.sha256(unchanged_body).hexdigest(),
    }
    monkeypatch.setattr(
        builtins,
        "_RETIRED_BUILTIN_SEED_HASHES",
        retired,
        raising=False,
    )
    unchanged = tmp_path / "engineer" / "unchanged.md"
    edited = tmp_path / "engineer" / "edited.md"
    edited.parent.mkdir(parents=True)
    unchanged.write_bytes(unchanged_body)
    edited.write_bytes(edited_body)

    removed = retire_orphaned_builtin_seeds(tmp_path)

    assert removed == ["engineer/edited.md", "engineer/unchanged.md"]
    assert not unchanged.exists()
    assert not edited.exists()
    archived = (
        tmp_path
        / "_retired_builtin_skills"
        / "engineer"
        / "edited.md.retired"
    )
    assert archived.read_bytes() == edited_body


def test_seeding_retires_existing_obsolete_skill(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus_skill.skills.builtins as builtins

    body = b"retired seed\n"
    monkeypatch.setattr(
        builtins,
        "_RETIRED_BUILTIN_SEED_HASHES",
        {"engineer/obsolete.md": hashlib.sha256(body).hexdigest()},
    )
    obsolete = tmp_path / "engineer" / "obsolete.md"
    obsolete.parent.mkdir(parents=True)
    obsolete.write_bytes(body)

    builtins.seed_builtin_skills(tmp_path)

    assert not obsolete.exists()


def test_seeding_refreshes_a_known_unmodified_legacy_builtin(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus_skill.skills.builtins as builtins

    relative = "engineer/example.md"
    old = "old factory body\n"
    new = "new factory body\n"
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True)
    destination.write_text(old, encoding="utf-8")
    monkeypatch.setattr(
        builtins,
        "iter_builtin_skill_texts",
        lambda: iter(((relative, new),)),
    )
    monkeypatch.setattr(
        builtins,
        "_LEGACY_BUILTIN_SEED_HASHES",
        {relative: hashlib.sha256(old.encode()).hexdigest()},
    )

    changed = builtins.seed_builtin_skills(tmp_path)

    assert changed[relative] is True
    assert destination.read_text(encoding="utf-8") == new


def test_seeding_refreshes_manifest_owned_builtin_but_preserves_user_edit(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus_skill.skills.builtins as builtins

    relative = "engineer/example.md"
    bodies = iter(("factory v1\n", "factory v2\n", "factory v3\n"))
    monkeypatch.setattr(
        builtins,
        "iter_builtin_skill_texts",
        lambda: iter(((relative, next(bodies)),)),
    )

    builtins.seed_builtin_skills(tmp_path)
    builtins.seed_builtin_skills(tmp_path)
    destination = tmp_path / relative
    assert destination.read_text(encoding="utf-8") == "factory v2\n"

    destination.write_text("operator edit\n", encoding="utf-8")
    changed = builtins.seed_builtin_skills(tmp_path)

    assert changed[relative] is False
    assert destination.read_text(encoding="utf-8") == "operator edit\n"


def test_research_playbooks_are_owned_only_by_research_vertical() -> None:
    common = dict(iter_builtin_skill_texts())
    research = dict(iter_vertical_skill_texts("research"))

    assert "engineer/idea-discovery.md" not in common
    assert "reviewer/experiment-results-review.md" not in common
    assert "engineer/idea-discovery.md" in research
    assert "reviewer/experiment-results-review.md" in research


def test_global_seeding_retires_manifest_owned_moved_research_skill(
    tmp_path,
) -> None:
    relative = "engineer/idea-discovery.md"
    body = "old factory research skill\n"
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True)
    destination.write_text(body, encoding="utf-8")
    (tmp_path / ".argus-builtin-seeds.json").write_text(
        json.dumps({relative: hashlib.sha256(body.encode()).hexdigest()}),
        encoding="utf-8",
    )

    seed_builtin_skills(tmp_path)

    assert not destination.exists()


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


def test_reference_corpora_are_assets_not_matchable_skills(tmp_path) -> None:
    names = {name for name, _text in iter_builtin_skill_texts()}

    assert not any("/references/" in f"/{name}" for name in names)
    seed_vertical_skills(tmp_path, "research", overwrite=True)
    # The owning idea-discovery Skill opens these paths directly. Excluding
    # them from matching must not exclude them from the runtime cache.
    assert (
        tmp_path
        / "engineer/references/ideation/anti-patterns.md"
    ).is_file()


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


def test_seed_for_vertical_preserves_operator_edit_without_overwrite(
    tmp_path,
) -> None:
    path = tmp_path / "engineer" / "quant-factor-loop.md"
    path.parent.mkdir(parents=True)
    path.write_text("operator-owned quant workflow\n", encoding="utf-8")

    changed = seed_builtin_skills_for_vertical(tmp_path, "quant")

    assert changed["engineer/quant-factor-loop.md"] is False
    assert path.read_text(encoding="utf-8") == "operator-owned quant workflow\n"


def test_seed_for_vertical_keeps_general_skills_without_research_leakage(
    tmp_path,
) -> None:
    seed_builtin_skills_for_vertical(tmp_path, "quant", overwrite=True)
    assert (tmp_path / "engineer" / "argus-engineer-role.md").exists()
    assert not (tmp_path / "reviewer" / "experiment-plan-review.md").exists()


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

    assert RESEARCH_SKILLS.issubset(written)
    assets = set(written) - RESEARCH_SKILLS
    assert assets
    assert all("/references/" in f"/{name}" for name in assets)


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

    removed = remove_unmodified_inactive_context_skill_seeds(
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

    removed = remove_unmodified_inactive_context_skill_seeds(tmp_path, None)

    assert set(removed) == MATH_SKILLS
    assert not any((tmp_path / filename).exists() for filename in MATH_SKILLS)
