"""Contract tests for built-in skill files.

Lock in two invariants:

1. Every matchable built-in skill markdown (excluding packaged ``references/``
   corpora) has YAML frontmatter with at minimum ``name`` and ``description``.
2. The three skills copied from ARIS (``citation-audit``,
   ``paper-claim-audit``, ``figure-spec``) are present and well-formed,
   and the figure-spec renderer script is importable + runs.

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

BUILTIN_ROOT = Path(__file__).resolve().parents[1] / "argus_skill" / "builtin_skills"


def _iter_skill_md_files() -> list[Path]:
    return [
        path
        for path in BUILTIN_ROOT.rglob("*.md")
        if "references" not in path.relative_to(BUILTIN_ROOT).parts
    ]


def _parse_frontmatter(text: str) -> dict[str, str] | None:
    """Cheap YAML frontmatter parser — accepts the subset our skills use
    (``key: value`` lines between two ``---`` delimiters)."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    block = text[4:end]
    out: dict[str, str] = {}
    for line in block.splitlines():
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


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
    assert failures == [], "Skills with invalid frontmatter:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize(
    "skill_path,expected_name",
    [
        ("engineer/citation-audit.md", "Citation Audit"),
        ("engineer/paper-claim-audit.md", "Paper Claim Audit"),
        ("engineer/figure-spec.md", "Figure Spec (deterministic SVG)"),
    ],
)
def test_aris_adapted_skills_are_present(skill_path: str, expected_name: str) -> None:
    md = BUILTIN_ROOT / skill_path
    assert md.exists(), f"missing adapted skill: {skill_path}"
    fm = _parse_frontmatter(md.read_text(encoding="utf-8"))
    assert fm is not None
    assert fm["name"] == expected_name


def test_figure_renderer_script_is_present_and_importable() -> None:
    renderer = BUILTIN_ROOT / "engineer" / "figure_spec_scripts" / "figure_renderer.py"
    assert renderer.exists(), "figure_renderer.py missing — figure-spec skill is broken"
    # Subprocess-import so we don't pollute the parent process's modules.
    proc = subprocess.run(
        [sys.executable, "-c",
         f"import importlib.util, sys; "
         f"spec = importlib.util.spec_from_file_location('fr', '{renderer}'); "
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
    renderer = BUILTIN_ROOT / "engineer" / "figure_spec_scripts" / "figure_renderer.py"
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "title": "Smoke",
                "width": 400,
                "height": 200,
                "nodes": [
                    {"id": "a", "label": "A", "x": 100, "y": 100, "shape": "rounded", "color": 0},
                    {"id": "b", "label": "B", "x": 300, "y": 100, "shape": "rounded", "color": 1},
                ],
                "edges": [{"from": "a", "to": "b", "label": "go"}],
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
    assert ">A<" in body
    assert ">B<" in body


def test_figure_renderer_is_deterministic(tmp_path: Path) -> None:
    """Same spec → byte-identical SVG. This is the core promise of the
    figure-spec skill vs AI image generation."""
    renderer = BUILTIN_ROOT / "engineer" / "figure_spec_scripts" / "figure_renderer.py"
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({"title": "Det", "width": 300, "height": 200,
                    "nodes": [{"id": "x", "label": "X", "x": 100, "y": 100, "color": 0}],
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
    from argus_skill.skills.builtins import seed_builtin_skills

    seed_builtin_skills(tmp_path)
    renderer = tmp_path / "engineer" / "figure_spec_scripts" / "figure_renderer.py"
    assert renderer.exists(), (
        "figure_renderer.py was NOT seeded into the workspace — the "
        "bundled-script copy path is broken"
    )
    # Sanity: the seeded copy is byte-identical to the in-tree source
    in_tree = BUILTIN_ROOT / "engineer" / "figure_spec_scripts" / "figure_renderer.py"
    assert renderer.read_bytes() == in_tree.read_bytes()


def test_plan_review_skill_has_rl_config_sanity_section() -> None:
    """The plan-review skill must teach the L2 reviewer to reject
    structurally-unlearnable RL configs at the plan stage (before GPU spend).
    """

    md = BUILTIN_ROOT / "reviewer" / "experiment-plan-review.md"
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
