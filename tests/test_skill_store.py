"""Smoke tests for the SkillStore (matcher path with MemoryBackend)."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.skills.store import Skill, SkillStore


def _write_skill(skills_dir: Path, name: str, description: str, category: str) -> None:
    skill = Skill(
        name=name,
        description=description,
        category=category,
        content=f"## When to use\n- {category} tasks\n\n## How to solve\n- step 1\n",
        version=1,
        created_at="2026-05-03T00:00:00+00:00",
    )
    SkillStore(skills_dir).save(skill)


def test_save_and_list(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "Foo", "do foo", "foo")
    _write_skill(skills_dir, "Bar", "do bar", "bar")
    summaries = SkillStore(skills_dir).list_summaries()
    names = sorted(s["name"] for s in summaries)
    assert names == ["Bar", "Foo"]


def test_skill_parse_accepts_quoted_semver_frontmatter() -> None:
    skill = Skill.parse(
        "---\n"
        "name: semver skill\n"
        "description: accepts quoted versions\n"
        "category: test\n"
        'version: "2.0"\n'
        "---\n\n"
        "body\n",
        "semver.md",
    )

    assert skill.version == 2


def test_find_relevant_returns_high_fit_skill(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "set-up-nginx", "configure nginx", "nginx")
    _write_skill(skills_dir, "audit-html", "review HTML", "html")

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(
        message=json.dumps({
            "matched": [{"name": "set-up-nginx", "fit": "high", "why": "exact"}],
        }),
    ))
    store = SkillStore(skills_dir, runner=backend, matcher_model="m")
    matched, _ = store.find_relevant("install nginx and serve a static site")
    assert matched is not None
    assert len(matched) == 1
    assert matched[0].name == "set-up-nginx"


def test_find_relevant_drops_medium_fit(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "set-up-nginx", "configure nginx", "nginx")

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(
        message=json.dumps({
            "matched": [{"name": "set-up-nginx", "fit": "medium", "why": "adjacent"}],
        }),
    ))
    store = SkillStore(skills_dir, runner=backend, matcher_model="m")
    matched, _ = store.find_relevant("audit some HTML for sanitization")
    assert matched is None


def test_save_distilled_extracts_name_and_description(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    raw = (
        "## Title\nProvision NGINX site\n\n"
        "## Description\nServe a static site with nginx.\n\n"
        "## Category\nnginx\n\n"
        "## When to use\n- need to host static content\n\n"
        "## When NOT to use\n- you need dynamic backend\n\n"
        "## How to solve\n- install nginx\n- write conf\n- enable site\n"
    )
    store = SkillStore(skills_dir)
    skill = store.save_distilled(
        task_description="set up an nginx site",
        raw_distill_output=raw,
        enforce_quality_gate=False,
    )
    assert skill is not None
    assert skill.name == "Provision NGINX site"
    assert skill.description == "Serve a static site with nginx."
    assert skill.category == "nginx"
    assert "set up an nginx site" in skill.task_history
    assert Path(skill.path).exists()


def test_find_relevant_runner_failure_returns_no_match(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "set-up-nginx", "configure nginx static site", "nginx")

    class BoomBackend:
        def run_exec(self, **kwargs):  # noqa: ARG002 — protocol stub
            raise RuntimeError("boom")

    store = SkillStore(skills_dir, runner=BoomBackend(), matcher_model="m")
    matched, _ = store.find_relevant("install nginx and serve a static site")
    assert matched is None


def test_find_relevant_fatal_matcher_result_returns_no_match(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "set-up-nginx", "configure nginx static site", "nginx")

    backend = MemoryBackend()
    backend.queue(
        "matcher",
        CannedResponse(
            exit_code=1,
            fatal_error=(
                "unexpected status 404 Not Found: "
                "The API deployment for this resource does not exist."
            ),
        ),
    )
    store = SkillStore(skills_dir, runner=backend, matcher_model="m")

    matched, tokens = store.find_relevant("install nginx and serve a static site")

    assert matched is None
    assert tokens == 0
    assert store.last_match_input_tokens == 0
    assert store.last_match_output_tokens == 0


def test_find_relevant_fatal_without_keyword_match_returns_none(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "audit-html", "review HTML", "html")

    backend = MemoryBackend()
    backend.queue(
        "matcher",
        CannedResponse(exit_code=1, fatal_error="502 Bad Gateway"),
    )
    store = SkillStore(skills_dir, runner=backend, matcher_model="m")

    matched, tokens = store.find_relevant("install nginx and serve a static site")

    assert matched is None
    assert tokens == 0
    assert store.last_match_input_tokens == 0
    assert store.last_match_output_tokens == 0


def test_find_relevant_cache_hit_resets_previous_token_counts(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "set-up-nginx", "configure nginx static site", "nginx")

    backend = MemoryBackend()
    backend.queue(
        "matcher",
        CannedResponse(
            message=json.dumps({
                "matched": [{"name": "set-up-nginx", "fit": "high", "why": "exact"}],
            }),
            input_tokens=101,
            cached_input_tokens=11,
            output_tokens=7,
        ),
    )
    store = SkillStore(skills_dir, runner=backend, matcher_model="m")

    matched, tokens = store.find_relevant("install nginx and serve a static site")
    assert matched is not None
    assert tokens == 108
    assert store.last_match_cached_input_tokens == 11

    matched_again, tokens_again = store.find_relevant(
        "install nginx and serve a static site"
    )
    assert matched_again is not None
    assert tokens_again == 0
    assert store.last_match_input_tokens == 0
    assert store.last_match_cached_input_tokens == 0
    assert store.last_match_output_tokens == 0
