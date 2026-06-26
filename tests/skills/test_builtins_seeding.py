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

import pytest

from argus_skill.skills.builtins import (
    _validate_builtin,
    iter_builtin_skill_texts,
    iter_vertical_skill_texts,
    seed_builtin_skills_for_vertical,
    vertical_skill_source_path,
)

QUANT_SKILLS = {
    "engineer/quant-factor-loop.md",
    "reviewer/quant-factor-report-review.md",
}


def test_iter_vertical_skill_texts_quant() -> None:
    got = {name for name, _ in iter_vertical_skill_texts("quant")}
    assert got == QUANT_SKILLS


def test_iter_vertical_skill_texts_unknown_or_skill_less_is_empty() -> None:
    assert list(iter_vertical_skill_texts("nope")) == []
    # research ships no own skills dir -> empty (fail-open).
    assert list(iter_vertical_skill_texts("research")) == []


def test_vertical_skill_source_path_rejects_injection() -> None:
    for bad in ("", "a/b", "..", ".hidden", "x\\y"):
        with pytest.raises(ValueError):
            vertical_skill_source_path(bad)


def test_builtin_quant_files_are_pointer_stubs() -> None:
    texts = dict(iter_builtin_skill_texts())
    for rel in QUANT_SKILLS:
        assert rel in texts, f"{rel} stub missing from builtin_skills"
        assert "MOVED" in texts[rel]
        assert "verticals/quant/skills" in texts[rel]


def test_all_builtins_valid_including_stubs() -> None:
    # Every bundled .md (stubs included) must parse with a name+description,
    # else the seeding pipeline's _validate_builtin would raise at runtime.
    for name, text in iter_builtin_skill_texts():
        if name.endswith(".md"):
            _validate_builtin(name, text)


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


def test_seed_for_research_does_not_pull_quant_real_body(tmp_path) -> None:
    # A vertical that does not own the quant skill keeps the builtin stub
    # (no cross-vertical leakage of domain skills).
    seed_builtin_skills_for_vertical(tmp_path, "research", overwrite=True)
    stub = tmp_path / "reviewer" / "quant-factor-report-review.md"
    assert stub.exists()
    assert "MOVED" in stub.read_text(encoding="utf-8")
