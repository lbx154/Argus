"""Vertical-aware builtin-skill seeding."""
from __future__ import annotations

import hashlib
import json

import pytest

from argus.skills.builtins import (
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
    "engineer/citation-check.md",
    "engineer/claims-against-evidence.md",
    "engineer/figure_spec_scripts/figure_renderer.py",
    "engineer/hypothesis-implementation-contract.md",
    "engineer/implementation-brief.md",
    "engineer/write-for-review.md",
    "engineer/method-card.md",
    "engineer/method_card_template.md",
    "engineer/executable-spec.md",
    "engineer/delta-on-reference.md",
    "engineer/research-grind.md",
    "engineer/research-timeline.md",
    "engineer/suspect-the-setup.md",
    "engineer/figure_spec_scripts/paper_chart_style.py",
    "engineer/figure_spec_scripts/paper_charts.py",
    "engineer/figure_spec_scripts/echarts_figure.py",
    "engineer/figure_spec_scripts/pptx_export.py",
    "engineer/paper-framework-figure-studio.md",
    "engineer/research-visualization-router.md",
    "engineer/research_visual_scripts/browser_render.py",
    "research-idea-playbook.md",
    "research-experiment-playbook.md",
    "research-paper-playbook.md",
    "research-review-playbook.md",
    "reviewer/reading-the-evidence.md",
    "reviewer/strongest-argument-against.md",
}
_RESEARCH_MOVE_MARKER = json.loads(
    (
        vertical_skill_source_path("research")
        / ".moved-from-global.json"
    ).read_text(encoding="utf-8")
)
RESEARCH_MOVED_SKILLS = set(
    path
    for path in (
        _RESEARCH_MOVE_MARKER.get("paths", ())
        if isinstance(_RESEARCH_MOVE_MARKER, dict)
        else _RESEARCH_MOVE_MARKER
    )
    if (vertical_skill_source_path("research") / path).is_file()
)
RESEARCH_SKILLS = RESEARCH_BASE_SKILLS | RESEARCH_MOVED_SKILLS | {
    "engineer/venue-paper-drafting.md",
    "engineer/venue-format-preflight.md",
    "engineer/academic-vector-figures.md",
    "reviewer/venue-academic-language-review.md",
    "engineer/infrastructure-landscape-survey.md",
    "engineer/framework-stand-up-pilot.md",
    "engineer/recipe-anchored-tuning.md",
    "reviewer/infrastructure-choice-review.md",
}


def test_iter_vertical_skill_texts_math() -> None:
    got = {name for name, _ in iter_vertical_skill_texts("math")}
    assert got == MATH_SKILLS


def test_iter_vertical_skill_texts_unknown_or_skill_less_is_empty() -> None:
    assert list(iter_vertical_skill_texts("nope")) == []
    software = dict(iter_vertical_skill_texts("software"))
    assert set(software) == {
        "engineer/formal-verification/verus-spec-generation-and-repair.md",
        "engineer/software-change-implementation.md",
        "manager/formal-verification/verus-spec-generation-and-repair.md",
        "manager/software-project-grounding.md",
        "planner/formal-verification/verus-spec-generation-and-repair.md",
        "planner/software-project-grounding.md",
        "reviewer/formal-verification/verus-spec-generation-and-repair.md",
        "reviewer/software-change-review.md",
        "verus-spec-generation-and-repair.md",
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
    # that vertical ONLY: a kernel playbook or a Lean proof recipe must not
    # cost a software or paper project summary tokens on every match.
    #
    # This used to be worked around with pointer stubs that stayed behind in
    # builtin_skills/. Stubs are candidates too — the seeding path already
    # skips them for the owning vertical, so they were pure dead weight for
    # everyone else. Deleting the skill from the flat pool is the fix; this
    # guard keeps it deleted.
    from argus.skills.vertical_select import VERTICALS

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
    assert "Return the completed work to the normal mission" in common["agent-team-lead.md"]


def test_machine_specific_nanochat_playbook_seeds_stay_retired() -> None:
    # The H100 nanochat traces once lived in the flat builtin pool; operator
    # libraries seeded before they were retired still carry copies, so the
    # retirement hashes must survive the vertical's move to argus-verticals.
    assert RETIRED_NANOCHAT_SKILLS <= _RETIRED_BUILTIN_SEED_HASHES.keys()


def test_retire_orphaned_builtin_seeds_archives_edited_copies(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus.skills.builtins as builtins

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
    import argus.skills.builtins as builtins

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


def test_seeding_refreshes_a_known_unmodified_legacy_builtin(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus.skills.builtins as builtins

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
    import argus.skills.builtins as builtins

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

    assert "research-idea-playbook.md" not in common
    assert "reviewer/experiment-results-review.md" not in common
    assert "research-idea-playbook.md" in research
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
    # The Idea playbook may open these paths on demand. Excluding
    # them from matching must not exclude them from the runtime cache.
    assert (
        tmp_path
        / "engineer/references/ideation/anti-patterns.md"
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


def test_seed_for_research_does_not_pull_another_verticals_skills(tmp_path) -> None:
    # A vertical that does not own the math skills must see no trace of them:
    # not the real body (cross-vertical leakage) and no pointer stub either.
    seed_builtin_skills_for_vertical(tmp_path, "research", overwrite=True)
    for relative in MATH_SKILLS:
        assert not (tmp_path / relative).exists(), relative
    assert (tmp_path / "engineer" / "research-visualization-router.md").is_file()


# --- seeds of verticals that left the package -------------------------------


def test_moved_vertical_seed_table_names_only_departed_verticals() -> None:
    import re

    import argus.skills.builtins as builtins
    from argus.skills.vertical_select import VERTICALS

    table = builtins._MOVED_VERTICAL_SEED_HASHES
    assert set(table) <= {
        "quant", "kernelbench", "speedrun", "nanogpt_speedrun", "nanochat", "chip_design",
        "digital_circuit", "digital_circuit_benchmark", "fiction_writing", "prose",
        "modern_poetry", "classical_poetry", "literary_editor", "medical", "materials",
        "physics", "ale_last_exam",
    }
    assert set(table).isdisjoint(VERTICALS)
    current = {name for v in VERTICALS for name, _ in iter_vertical_skill_texts(v)}
    current |= {name for name, _ in iter_builtin_skill_texts()}
    for vertical, seeds in table.items():
        assert seeds, vertical
        for relative, digest in seeds.items():
            assert re.fullmatch(r"[0-9a-f]{64}", digest), (vertical, relative)
            assert relative not in current, f"{vertical}:{relative} is still shipped"


def test_pre_split_seeds_of_an_uninstalled_vertical_are_pruned_but_edits_survive(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus.skills.builtins as builtins

    factory = "factory quant playbook as seeded on dev\n"
    monkeypatch.setattr(builtins, "_MOVED_VERTICAL_SEED_HASHES", {
        "quant": {
            "engineer/quant-factor-loop.md": hashlib.sha256(factory.encode()).hexdigest(),
            "engineer/kline-chart.md": hashlib.sha256(factory.encode()).hexdigest(),
        },
    })
    pristine = tmp_path / "engineer" / "quant-factor-loop.md"
    edited = tmp_path / "engineer" / "kline-chart.md"
    pristine.parent.mkdir(parents=True)
    pristine.write_text(factory.replace("\n", "\r\n"), encoding="utf-8", newline="")  # CRLF copy
    edited.write_text(factory + "operator note\n", encoding="utf-8")

    removed = builtins.retire_orphaned_builtin_seeds(tmp_path)

    assert "engineer/quant-factor-loop.md" in removed
    assert not pristine.exists()
    assert edited.read_text(encoding="utf-8") == factory + "operator note\n"
    assert not (tmp_path / "_retired_builtin_skills").exists()


def test_seeds_of_an_installed_community_vertical_are_left_to_the_plugin(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus.skills.builtins as builtins
    from argus.skills import vertical_select

    factory = "factory quant playbook\n"
    monkeypatch.setattr(builtins, "_MOVED_VERTICAL_SEED_HASHES", {
        "quant": {"engineer/quant-factor-loop.md": hashlib.sha256(factory.encode()).hexdigest()},
    })
    monkeypatch.setattr(
        vertical_select, "available_verticals",
        lambda: (*vertical_select.VERTICALS, "quant"),
    )
    path = tmp_path / "engineer" / "quant-factor-loop.md"
    path.parent.mkdir(parents=True)
    path.write_text(factory, encoding="utf-8")

    assert builtins.retire_uninstalled_vertical_seeds(tmp_path) == []
    assert path.exists()
