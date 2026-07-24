"""Draft-first outline: paper/DRAFT_OUTLINE.md as the single source of truth.

Why this exists
---------------

In agent-multimodal-reasoning-v1 the planner authorized 14 consecutive
"Bounded overlap paper-drafting mission while current_stage stays run"
missions. Engineer drafted the paper *while* runs were still in flight,
which created a moving target: figures got commissioned ad-hoc,
experiments got added ad-hoc, and ``paper/main.tex`` slowly diverged
from what the run matrix actually contained. Reviewer then spent 14
rounds rolling back to fix the drift.

The fix recommended by the operator's classmate is structural:

1. Write a Draft text frame FIRST, with every figure and every experiment
   stubbed out as a placeholder that records its intended style /
   reference / experiment-id.
2. Treat the placeholders as the binding contract: figures and
   experiments downstream may only fill placeholders, not invent new
   slots.
3. Stop scattering small markdown notes in the project root; everything
   feeds back into the Draft.

This module is the harness side of (1) and (2). It defines:

* the canonical path ``paper/DRAFT_OUTLINE.md``;
* a YAML-frontmatter + markdown body schema;
* a validator that distinguishes "outline missing", "outline unfilled",
  and "outline filled but downstream uses unknown id";
* a cross-check that, given a list of figure / experiment ids found
  elsewhere in the workspace (e.g. ``\\label{fig:...}`` in main.tex),
  reports which ones lack a placeholder.

This is *not* a hard gate. The validator returns structured issues;
``paper_structural_minimums`` decides their draft-time severity. We
specifically avoid a "you may not advance" gate because the operator's
philosophy is "soft critique-driven, not hard checklist-driven".

Schema
------

``paper/DRAFT_OUTLINE.md`` has YAML frontmatter and three sections:

```markdown
---
outline_version: 1
mission_sha: <sha of MISSION.md at outline creation>
---

## Sections
- title: Introduction
  goal: motivate the trap-vs-control framing
- title: Method
  goal: ...

## Figures
- id: F1_teaser
  style_ref: MMMU2024 Fig.1                # which paper/figure to copy from
  data_source: bench/dev_smoke/items.jsonl   # what feeds the figure
  caption_placeholder: "trap vs. control example for broken-scale family"
- id: F2_results_heatmap
  style_ref: MathVista2024 Tab.2 heatmap
  data_source: paper/artifacts/results_table.tsv
  caption_placeholder: "model x trap-family accuracy"

## Experiments
- id: E1_main_matrix
  cell_spec: 13 models x 5 trap families x 3 seeds
  expected_metric: trap-control gap (paired)
  n_seeds: 3
- id: E2_severity_ladder
  cell_spec: same models x severity in {1,2,3}
  expected_metric: monotonicity
  n_seeds: 3
```

The frontmatter is parsed strictly; the body is parsed permissively
(missing fields surface as issues, not exceptions).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

DRAFT_OUTLINE_PATH = Path("paper/DRAFT_OUTLINE.md")

# Sentinel: minimum number of placeholders the outline must contain to be
# treated as "filled". Below these the validator emits an
# ``outline_unfilled`` issue. Threshold values come from MISSION-typical
# requirements (≥6 figures, ≥3 experiments).
MIN_FIGURE_PLACEHOLDERS = 3
MIN_EXPERIMENT_PLACEHOLDERS = 1
MIN_SECTION_PLACEHOLDERS = 4

# Required frontmatter keys.
_REQUIRED_FRONTMATTER_KEYS = ("outline_version",)

# Required fields per placeholder type.
_REQUIRED_FIGURE_FIELDS = ("id", "style_ref", "data_source", "caption_placeholder")
_REQUIRED_EXPERIMENT_FIELDS = ("id", "cell_spec", "expected_metric")
_REQUIRED_SECTION_FIELDS = ("title",)


@dataclass(frozen=True)
class FigurePlaceholder:
    id: str
    style_ref: str = ""
    data_source: str = ""
    caption_placeholder: str = ""
    extras: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExperimentPlaceholder:
    id: str
    cell_spec: str = ""
    expected_metric: str = ""
    n_seeds: int = 0
    extras: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SectionPlaceholder:
    title: str
    goal: str = ""
    extras: dict = field(default_factory=dict)


@dataclass(frozen=True)
class OutlineIssue:
    severity: str         # "missing" | "unfilled" | "incomplete" | "orphan"
    code: str             # short stable id
    message: str          # human-readable
    placeholder_id: str = ""


@dataclass(frozen=True)
class DraftOutline:
    path: Path
    frontmatter: dict
    sections: tuple[SectionPlaceholder, ...] = ()
    figures: tuple[FigurePlaceholder, ...] = ()
    experiments: tuple[ExperimentPlaceholder, ...] = ()
    raw_text: str = ""

    def figure_ids(self) -> set[str]:
        return {f.id for f in self.figures if f.id}

    def experiment_ids(self) -> set[str]:
        return {e.id for e in self.experiments if e.id}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict, body text). Empty dict if no frontmatter."""
    if not text.startswith("---"):
        return {}, text
    m = re.match(r"---\n(.*?)\n---\n?", text, re.DOTALL)
    if not m:
        return {}, text
    front_raw = m.group(1)
    body = text[m.end():]
    front: dict = {}
    for line in front_raw.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        front[k.strip()] = v.strip()
    return front, body


_SECTION_RE = re.compile(r"^##\s+(\w[\w\s]*)\s*$", re.MULTILINE)


def _split_body_sections(body: str) -> dict[str, str]:
    """Group body markdown into ``{H2 title: section text}`` chunks.

    Titles are normalized to lowercase for matching downstream.
    """
    out: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(body))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        title = m.group(1).strip().lower()
        out[title] = body[start:end].strip()
    return out


_LIST_ITEM_RE = re.compile(r"^-\s+(.*)$")
_KV_RE = re.compile(r"^\s+([a-z_][a-z0-9_]*)\s*:\s*(.*)$")


def _parse_list_of_dicts(section_text: str) -> list[dict]:
    """Parse a permissive YAML-list-of-dicts under one H2.

    Items are introduced by ``- key: value`` and continued by indented
    ``  key: value`` lines. We do not depend on PyYAML.
    """
    items: list[dict] = []
    current: dict | None = None
    for raw in section_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m_item = _LIST_ITEM_RE.match(line)
        if m_item:
            if current is not None:
                items.append(current)
            current = {}
            inner = m_item.group(1)
            if ":" in inner:
                k, v = inner.split(":", 1)
                current[k.strip()] = v.strip()
            continue
        m_kv = _KV_RE.match(line)
        if m_kv and current is not None:
            current[m_kv.group(1)] = m_kv.group(2).strip()
    if current is not None:
        items.append(current)
    return items


def _coerce_int(v: object, default: int = 0) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def parse_outline(text: str, path: Path = DRAFT_OUTLINE_PATH) -> DraftOutline:
    """Parse outline text into a structured ``DraftOutline``. Permissive."""
    front, body = _split_frontmatter(text)
    sections_map = _split_body_sections(body)

    section_items = _parse_list_of_dicts(sections_map.get("sections", ""))
    figure_items = _parse_list_of_dicts(sections_map.get("figures", ""))
    experiment_items = _parse_list_of_dicts(sections_map.get("experiments", ""))

    sections = tuple(
        SectionPlaceholder(
            title=str(d.get("title", "")).strip(),
            goal=str(d.get("goal", "")).strip(),
            extras={k: v for k, v in d.items() if k not in {"title", "goal"}},
        )
        for d in section_items
    )
    figures = tuple(
        FigurePlaceholder(
            id=str(d.get("id", "")).strip(),
            style_ref=str(d.get("style_ref", "")).strip(),
            data_source=str(d.get("data_source", "")).strip(),
            caption_placeholder=str(d.get("caption_placeholder", "")).strip(),
            extras={k: v for k, v in d.items()
                    if k not in {"id", "style_ref", "data_source", "caption_placeholder"}},
        )
        for d in figure_items
    )
    experiments = tuple(
        ExperimentPlaceholder(
            id=str(d.get("id", "")).strip(),
            cell_spec=str(d.get("cell_spec", "")).strip(),
            expected_metric=str(d.get("expected_metric", "")).strip(),
            n_seeds=_coerce_int(d.get("n_seeds", 0)),
            extras={k: v for k, v in d.items()
                    if k not in {"id", "cell_spec", "expected_metric", "n_seeds"}},
        )
        for d in experiment_items
    )
    return DraftOutline(
        path=path,
        frontmatter=front,
        sections=sections,
        figures=figures,
        experiments=experiments,
        raw_text=text,
    )


def load_outline(project_root: Path) -> DraftOutline | None:
    p = project_root / DRAFT_OUTLINE_PATH
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_outline(text, path=p)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_outline(outline: DraftOutline | None) -> list[OutlineIssue]:
    """Return structured issues describing what's wrong with the outline.

    Issues use stable codes so callers (stage_check, structural_minimums)
    can decide severity independently.
    """
    issues: list[OutlineIssue] = []
    if outline is None:
        issues.append(OutlineIssue(
            severity="missing",
            code="outline_missing",
            message=f"{DRAFT_OUTLINE_PATH} is absent — Draft-first contract is "
                    f"not in place. Create it before authorizing run/draft "
                    f"work so figures and experiments have placeholders to "
                    f"fill.",
        ))
        return issues

    # frontmatter
    for key in _REQUIRED_FRONTMATTER_KEYS:
        if key not in outline.frontmatter:
            issues.append(OutlineIssue(
                severity="incomplete",
                code="frontmatter_field_missing",
                message=f"frontmatter missing required key: {key}",
            ))

    # placeholder counts
    if len(outline.sections) < MIN_SECTION_PLACEHOLDERS:
        issues.append(OutlineIssue(
            severity="unfilled",
            code="sections_underfilled",
            message=f"only {len(outline.sections)} section placeholders "
                    f"(need ≥ {MIN_SECTION_PLACEHOLDERS})",
        ))
    if len(outline.figures) < MIN_FIGURE_PLACEHOLDERS:
        issues.append(OutlineIssue(
            severity="unfilled",
            code="figures_underfilled",
            message=f"only {len(outline.figures)} figure placeholders "
                    f"(need ≥ {MIN_FIGURE_PLACEHOLDERS})",
        ))
    if len(outline.experiments) < MIN_EXPERIMENT_PLACEHOLDERS:
        issues.append(OutlineIssue(
            severity="unfilled",
            code="experiments_underfilled",
            message=f"only {len(outline.experiments)} experiment placeholders "
                    f"(need ≥ {MIN_EXPERIMENT_PLACEHOLDERS})",
        ))

    # per-placeholder field check
    for f in outline.figures:
        for field_name in _REQUIRED_FIGURE_FIELDS:
            if not getattr(f, field_name, ""):
                issues.append(OutlineIssue(
                    severity="incomplete",
                    code="figure_field_missing",
                    placeholder_id=f.id,
                    message=f"figure '{f.id}' missing field: {field_name}",
                ))
    for e in outline.experiments:
        for field_name in _REQUIRED_EXPERIMENT_FIELDS:
            if not getattr(e, field_name, ""):
                issues.append(OutlineIssue(
                    severity="incomplete",
                    code="experiment_field_missing",
                    placeholder_id=e.id,
                    message=f"experiment '{e.id}' missing field: {field_name}",
                ))
    for s in outline.sections:
        for field_name in _REQUIRED_SECTION_FIELDS:
            if not getattr(s, field_name, ""):
                issues.append(OutlineIssue(
                    severity="incomplete",
                    code="section_field_missing",
                    message=f"section missing field: {field_name}",
                ))

    # duplicate ids
    seen_fig: set[str] = set()
    for f in outline.figures:
        if f.id and f.id in seen_fig:
            issues.append(OutlineIssue(
                severity="incomplete",
                code="figure_id_duplicate",
                placeholder_id=f.id,
                message=f"duplicate figure id: {f.id}",
            ))
        seen_fig.add(f.id)
    seen_exp: set[str] = set()
    for e in outline.experiments:
        if e.id and e.id in seen_exp:
            issues.append(OutlineIssue(
                severity="incomplete",
                code="experiment_id_duplicate",
                placeholder_id=e.id,
                message=f"duplicate experiment id: {e.id}",
            ))
        seen_exp.add(e.id)

    return issues


def cross_check_figure_ids(
    outline: DraftOutline | None,
    main_tex_figure_ids: Iterable[str],
) -> list[OutlineIssue]:
    """Return ``orphan`` issues for figures present in main.tex but missing
    from outline.

    Figures present in outline but missing from main.tex are *not* flagged
    here — those are normal during in-flight drafting.
    """
    if outline is None:
        return []
    out_ids = outline.figure_ids()
    issues: list[OutlineIssue] = []
    for fid in main_tex_figure_ids:
        if not fid:
            continue
        if fid not in out_ids:
            issues.append(OutlineIssue(
                severity="orphan",
                code="figure_orphan",
                placeholder_id=fid,
                message=f"figure '{fid}' appears in main.tex but has no "
                        f"placeholder in {DRAFT_OUTLINE_PATH} — was it added "
                        f"ad-hoc? Add a placeholder retroactively or remove.",
            ))
    return issues


__all__ = [
    "DRAFT_OUTLINE_PATH",
    "MIN_FIGURE_PLACEHOLDERS",
    "MIN_EXPERIMENT_PLACEHOLDERS",
    "MIN_SECTION_PLACEHOLDERS",
    "DraftOutline",
    "FigurePlaceholder",
    "ExperimentPlaceholder",
    "SectionPlaceholder",
    "OutlineIssue",
    "load_outline",
    "parse_outline",
    "validate_outline",
    "cross_check_figure_ids",
]
