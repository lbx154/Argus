"""Tests for the unified role-mission skill matcher.

Engineer, reviewer, and planner all run the same ``match_role_skills``
primitive. Each role matches against its own pool PLUS its cross-role
reference pool, but other-role skills only ever come back as read-only
``reference_skills`` — never as the operative ``primary`` (which drives
distill-on-miss and writeback). These tests pin that partitioning, the
``exclude_files`` dedupe, and the role-aware cache key.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.skills.role_match import match_role_skills, render_skill_playbook
from argus_skill.skills.store import Skill, SkillStore


def _write_role_skill(skills_dir: Path, role: str, slug: str,
                      name: str, description: str) -> None:
    """Write a skill markdown file into a role subdir (or top-level)."""
    target_dir = skills_dir if role == "general" else skills_dir / role
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / f"{slug}.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "category: test\n"
        "version: 1\n"
        "---\n\n"
        "## When to use\n- test tasks\n\n## How to solve\n- step 1\n",
        encoding="utf-8",
    )


def test_canonical_playbook_renderer_injects_all_high_fit_skills(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")

    def make_skill(name: str, description: str) -> Skill:
        return Skill(
            name=name,
            description=description,
            category="demo",
            content="## When to use\n- demo tasks\n\n## How to solve\n- step 1\n",
            version=1,
            created_at="2026-05-03T00:00:00+00:00",
        )

    one = make_skill("Alpha Skill", "do alpha")
    two = make_skill("Beta Skill", "do beta")

    single = render_skill_playbook(store, [one])
    assert "How to solve" in single
    assert "candidates, not orders" not in single
    assert "### Candidate skill:" not in single

    multi = render_skill_playbook(store, [one, two])
    assert "Alpha Skill" in multi
    assert "Beta Skill" in multi
    assert "candidates, not orders" in multi
    assert "### Candidate skill: Alpha Skill" in multi
    assert "### Candidate skill: Beta Skill" in multi

    assert render_skill_playbook(store, []) == ""


class _CountingBackend(MemoryBackend):
    """MemoryBackend that counts matcher invocations."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def run_exec(self, *args, **kwargs):  # type: ignore[override]
        self.calls += 1
        return super().run_exec(*args, **kwargs)


def test_engineer_sees_reviewer_skill_as_reference_only(tmp_path: Path) -> None:
    """A reviewer skill can reach the engineer only as a read-only reference.

    The lone skill lives under ``reviewer/``. With cross-role visibility the
    engineer's matcher pool includes reviewer skills, so the matcher DOES run;
    but a reviewer skill must surface as a ``reference`` — never ``primary`` —
    so it never drives engineer distill-on-miss or writeback.
    """
    skills_dir = tmp_path / "skills"
    _write_role_skill(skills_dir, "reviewer", "peer-review",
                      "peer-review", "review a paper")
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(
        message=json.dumps({
            "matched": [{"name": "peer-review", "fit": "high", "why": "x"}],
        }),
    ))
    store = SkillStore(skills_dir, runner=backend, matcher_model="m")

    match = match_role_skills(store, role="engineer", task="review this paper")

    assert match.primary is None  # reviewer skill is never the engineer's own
    assert [s.name for s in match.primary_skills] == []
    assert [s.name for s in match.reference_skills] == ["peer-review"]
    assert "Reference" in match.block


def test_planner_sees_other_roles_as_reference_only(tmp_path: Path) -> None:
    """Planner cross-reads engineer/reviewer skills, but only as references."""
    skills_dir = tmp_path / "skills"
    _write_role_skill(skills_dir, "engineer", "deploy",
                      "deploy", "ship a service")
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(
        message=json.dumps({
            "matched": [{"name": "deploy", "fit": "high", "why": "x"}],
        }),
    ))
    store = SkillStore(skills_dir, runner=backend, matcher_model="m")

    match = match_role_skills(store, role="planner", task="plan the next sprint")

    assert match.primary is None  # no planner-owned skill exists
    assert [s.name for s in match.reference_skills] == ["deploy"]


def test_empty_scope_short_circuits_without_backend_call(tmp_path: Path) -> None:
    """When neither own nor reference pool has a skill, no matcher runs."""
    skills_dir = tmp_path / "skills"
    # Only a planner skill exists; the engineer neither owns nor references it.
    _write_role_skill(skills_dir, "planner", "roadmap",
                      "roadmap", "plan a roadmap")
    backend = _CountingBackend()
    store = SkillStore(skills_dir, runner=backend, matcher_model="m")

    match = match_role_skills(store, role="engineer", task="ship a service")

    assert match.skills == []
    assert match.primary is None
    assert match.block == ""
    assert backend.calls == 0  # empty scoped pool => no matcher call


def test_reviewer_match_returns_scoped_skill(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_role_skill(skills_dir, "reviewer", "results-review",
                      "results-review", "review experiment results")
    # An engineer skill: the reviewer now cross-reads engineer skills, but the
    # canned matcher returns only the reviewer skill, so it stays the sole
    # (primary) result here.
    _write_role_skill(skills_dir, "engineer", "deploy",
                      "deploy", "ship a service")

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(
        message=json.dumps({
            "matched": [
                {"name": "results-review", "fit": "high", "why": "exact"},
            ],
        }),
    ))
    store = SkillStore(skills_dir, runner=backend, matcher_model="m")

    match = match_role_skills(store, role="reviewer",
                             task="review the experiment results table")
    assert [s.name for s in match.skills] == ["results-review"]
    assert match.block  # rendered playbook present


def test_reviewer_exclude_files_dedupes_hardcoded_skill(tmp_path: Path) -> None:
    """A skill named in ``exclude_files`` is never returned by the matcher."""
    skills_dir = tmp_path / "skills"
    _write_role_skill(skills_dir, "reviewer", "fixed-role",
                      "fixed-role", "fixed reviewer role context")
    _write_role_skill(skills_dir, "reviewer", "results-review",
                      "results-review", "review experiment results")

    backend = MemoryBackend()
    # Even if the model tries to return the excluded skill, scoping must
    # prevent it from ever appearing in the candidate pool / result.
    backend.queue("matcher", CannedResponse(
        message=json.dumps({
            "matched": [
                {"name": "results-review", "fit": "high", "why": "exact"},
            ],
        }),
    ))
    store = SkillStore(skills_dir, runner=backend, matcher_model="m")

    match = match_role_skills(
        store, role="reviewer",
        task="review the experiment results",
        exclude_files={"fixed-role.md"},
    )
    names = [s.name for s in match.skills]
    assert "fixed-role" not in names
    assert "results-review" in names


def test_matcher_cache_is_role_scoped(tmp_path: Path) -> None:
    """Same task under the same role hits the cache (no second backend call)."""
    skills_dir = tmp_path / "skills"
    _write_role_skill(skills_dir, "engineer", "deploy",
                      "deploy", "ship a service")
    backend = _CountingBackend()
    backend.queue("matcher", CannedResponse(
        message=json.dumps({
            "matched": [{"name": "deploy", "fit": "high", "why": "exact"}],
        }),
    ))
    store = SkillStore(skills_dir, runner=backend, matcher_model="m")

    first = match_role_skills(store, role="engineer", task="deploy the service")
    second = match_role_skills(store, role="engineer", task="deploy the service")

    assert [s.name for s in first.skills] == ["deploy"]
    assert [s.name for s in second.skills] == ["deploy"]
    assert backend.calls == 1  # second call served from cache
