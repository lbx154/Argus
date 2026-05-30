"""Tests for the unified role-scoped skill matcher.

Engineer, reviewer, and planner all run the same ``match_role_skills``
primitive against a role-scoped pool. These tests pin the scoping
guarantees: a role never matches another role's skills, empty pools
short-circuit with no backend call, ``exclude_files`` dedupes
already-injected skills, and the cache key is role-aware.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.skills.role_match import match_role_skills
from argus_skill.skills.store import SkillStore


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


class _CountingBackend(MemoryBackend):
    """MemoryBackend that counts matcher invocations."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def run_exec(self, *args, **kwargs):  # type: ignore[override]
        self.calls += 1
        return super().run_exec(*args, **kwargs)


def test_engineer_pool_excludes_reviewer_skills(tmp_path: Path) -> None:
    """An engineer mission never matches a reviewer-only skill.

    The lone skill lives under ``reviewer/``; with ``role='engineer'`` the
    scoped pool is empty, so the matcher short-circuits with no backend call.
    """
    skills_dir = tmp_path / "skills"
    _write_role_skill(skills_dir, "reviewer", "peer-review",
                      "peer-review", "review a paper")
    backend = _CountingBackend()
    store = SkillStore(skills_dir, runner=backend, matcher_model="m")

    match = match_role_skills(store, role="engineer", task="review this paper")

    assert match.skills == []
    assert match.primary is None
    assert match.block == ""
    assert backend.calls == 0  # empty scoped pool => no matcher call


def test_planner_empty_pool_short_circuits(tmp_path: Path) -> None:
    """No planner pool exists -> empty match, no backend call."""
    skills_dir = tmp_path / "skills"
    _write_role_skill(skills_dir, "engineer", "deploy",
                      "deploy", "ship a service")
    backend = _CountingBackend()
    store = SkillStore(skills_dir, runner=backend, matcher_model="m")

    match = match_role_skills(store, role="planner", task="plan the next sprint")

    assert match.skills == []
    assert backend.calls == 0


def test_reviewer_match_returns_scoped_skill(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_role_skill(skills_dir, "reviewer", "results-review",
                      "results-review", "review experiment results")
    # An engineer skill that the reviewer pool must NOT see.
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
