"""Contract tests for built-in skill files.

Lock in two invariants:

1. Every matchable built-in skill markdown (excluding packaged ``references/``
   corpora) has YAML frontmatter with at minimum ``name`` and ``description``.
2. The retained ARIS-derived skills are present and well-formed, claim checking
   has one canonical implementation, and the figure renderer is importable.

This prevents accidental drift of the skill bundle and catches the
"someone added a skill without frontmatter so the matcher silently
ignores it" failure mode.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

BUILTIN_ROOT = Path(__file__).resolve().parents[1] / "argus_skill" / "builtin_skills"
RESEARCH_ROOT = (
    Path(__file__).resolve().parents[1]
    / "argus_skill"
    / "verticals"
    / "research"
    / "skills"
)


def _iter_skill_md_files() -> list[Path]:
    return [
        path
        for path in BUILTIN_ROOT.rglob("*.md")
        if "references" not in path.relative_to(BUILTIN_ROOT).parts
    ]


def _parse_frontmatter(text: str) -> dict[str, str] | None:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        return None
    loaded = yaml.safe_load(text[4:].split("\n---\n", 1)[0])
    return loaded if isinstance(loaded, dict) else None


def test_every_builtin_skill_has_frontmatter() -> None:
    failures: list[str] = []
    for md in _iter_skill_md_files():
        text = md.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        if fm is None:
            failures.append(f"{md.relative_to(BUILTIN_ROOT)}: no frontmatter")
            continue
        if not fm.get("name"):
            failures.append(f"{md.relative_to(BUILTIN_ROOT)}: missing name")
        if not fm.get("description"):
            failures.append(f"{md.relative_to(BUILTIN_ROOT)}: missing description")
        if set(fm) != {"name", "description"}:
            failures.append(f"{md.relative_to(BUILTIN_ROOT)}: extra fields {set(fm) - {'name', 'description'}}")
    assert failures == [], "Skills with invalid frontmatter:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize(
    "skill_path,expected_name",
    [
        ("engineer/citation-audit.md", "Citation Verification"),
        ("engineer/claims-evidence-audit.md", "Claim Check"),
        ("engineer/figure-spec.md", "Figure Spec (deterministic SVG)"),
    ],
)
def test_aris_adapted_skills_are_present(skill_path: str, expected_name: str) -> None:
    md = RESEARCH_ROOT / skill_path
    assert md.exists(), f"missing adapted skill: {skill_path}"
    fm = _parse_frontmatter(md.read_text(encoding="utf-8"))
    assert fm is not None
    assert fm["name"] == expected_name


def test_claim_check_requires_fresh_source_level_verification() -> None:
    text = (
        RESEARCH_ROOT / "engineer" / "claims-evidence-audit.md"
    ).read_text(encoding="utf-8")

    assert "fresh-context" in text
    assert "MATCH / MISMATCH / MISSING" in text
    assert "one reviewer thread per claim" in text
    assert "PAPER_CLAIM_AUDIT" not in text


def test_figure_renderer_script_is_present_and_importable() -> None:
    renderer = RESEARCH_ROOT / "engineer" / "figure_spec_scripts" / "figure_renderer.py"
    assert renderer.exists(), "figure_renderer.py missing — figure-spec skill is broken"
    # Subprocess-import so we don't pollute the parent process's modules.
    proc = subprocess.run(
        [sys.executable, "-c",
         f"import importlib.util, sys; "
         f"spec = importlib.util.spec_from_file_location('fr', {str(renderer)!r}); "
         f"mod = importlib.util.module_from_spec(spec); "
         f"spec.loader.exec_module(mod); "
         f"print('OK')"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"figure_renderer.py is not importable:\n"
        f"  stdout: {proc.stdout}\n  stderr: {proc.stderr}"
    )


def test_figure_renderer_round_trip_render(tmp_path: Path) -> None:
    renderer = RESEARCH_ROOT / "engineer" / "figure_spec_scripts" / "figure_renderer.py"
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "title": "Smoke",
                "canvas": {"width": 400, "height": 200},
                "nodes": [
                    {"id": "a", "label": "A", "x": 100, "y": 100, "shape": "rounded",
                     "fill": "#DBEAFE", "stroke": "#2563EB"},
                    {"id": "b", "label": "B", "x": 300, "y": 100, "shape": "rounded",
                     "fill": "#D1FAE5", "stroke": "#10B981"},
                ],
                "edges": [{"from": "a", "to": "b", "label": "go"}],
                "groups": [
                    {"id": "scope", "label": "Scope", "node_ids": ["a", "b"],
                     "fill": "#F3F4F6", "stroke": "#9CA3AF"}
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out.svg"

    proc = subprocess.run(
        [sys.executable, str(renderer), "render", str(spec), "--output", str(out)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    # Sanity: SVG with both nodes labeled
    assert body.startswith("<svg")
    assert 'viewBox="0 0 400 200"' in body
    assert ">A<" in body
    assert ">B<" in body
    assert ">Scope<" in body


def test_figure_renderer_is_deterministic(tmp_path: Path) -> None:
    """Same spec → byte-identical SVG. This is the core promise of the
    figure-spec skill vs AI image generation."""
    renderer = RESEARCH_ROOT / "engineer" / "figure_spec_scripts" / "figure_renderer.py"
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({"title": "Det", "canvas": {"width": 300, "height": 200},
                    "nodes": [{"id": "x", "label": "X", "x": 100, "y": 100,
                               "fill": "#DBEAFE", "stroke": "#2563EB"}],
                    "edges": []}),
        encoding="utf-8",
    )
    out1 = tmp_path / "a.svg"
    out2 = tmp_path / "b.svg"
    for out in (out1, out2):
        proc = subprocess.run(
            [sys.executable, str(renderer), "render", str(spec), "--output", str(out)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
    assert out1.read_bytes() == out2.read_bytes(), (
        "figure_renderer.py is non-deterministic; two runs of the same "
        "spec produced different SVG"
    )


def test_seed_builtin_skills_copies_bundled_scripts(tmp_path: Path) -> None:
    """seed_builtin_skills must copy *_scripts/ assets (e.g.
    figure_renderer.py) alongside the skill markdown. Without this the
    skill prompt would reference a script that's missing in the seeded
    project workspace."""
    from argus_skill.skills.builtins import seed_vertical_skills

    seed_vertical_skills(tmp_path, "research")
    renderer = tmp_path / "engineer" / "figure_spec_scripts" / "figure_renderer.py"
    assert renderer.exists(), (
        "figure_renderer.py was NOT seeded into the workspace — the "
        "bundled-script copy path is broken"
    )
    # Sanity: the seeded copy has identical source content. Git may check out
    # the source with CRLF on Windows while importlib.resources yields LF.
    in_tree = RESEARCH_ROOT / "engineer" / "figure_spec_scripts" / "figure_renderer.py"
    assert renderer.read_text(encoding="utf-8") == in_tree.read_text(encoding="utf-8")


def test_plan_review_skill_has_rl_config_sanity_section() -> None:
    """The plan-review skill must teach the L2 reviewer to reject
    structurally-unlearnable RL configs at the plan stage (before GPU spend).
    """

    md = RESEARCH_ROOT / "reviewer" / "experiment-plan-review.md"
    text = md.read_text(encoding="utf-8")
    # The scored 6th dimension + its output key.
    assert "RL training-configuration sanity" in text
    assert "rl_config_sanity" in text
    # The hard-blocker auto-fails for at-a-glance rejects.
    assert "RL post-training auto-fails" in text
    assert "num_generations" in text
    assert "max_completion_length" in text
    # Concrete length-budget yardsticks so the reviewer can actually JUDGE
    # "max_len too short" instead of eyeballing it.
    assert "p95" in text
    assert "Reference floors" in text
    assert "auto-reject" in text
    # Asymmetric-error stance: default to the max the budget allows.
    assert "as large as the context window" in text
    assert "floor, not a target" in text
    # Cross-references the in-flight collapse skill.
    assert "rl-training-collapse-diagnosis.md" in text


def test_research_review_uses_continuous_result_judgment() -> None:
    plan = (
        RESEARCH_ROOT / "reviewer" / "experiment-plan-review.md"
    ).read_text(encoding="utf-8")
    results = (
        RESEARCH_ROOT / "reviewer" / "experiment-results-review.md"
    ).read_text(encoding="utf-8")

    for text in (plan, results):
        assert "hard numeric" in text
        assert "scientifically" in text
        assert "meaningful dimension" in text
