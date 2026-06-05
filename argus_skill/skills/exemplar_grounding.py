"""Exemplar-grounding structural gate.

Forces the paper-exemplar-pdf-learning skill to actually produce the
artifacts it advertises. Closes the same loop as
``paper_structural_minimums`` did for figures: skill prompt says "study
2+ top-venue EMNLP/ACL papers and adapt their structure"; without a
harness gate, the agent skips it and freestyles the section order. The
v1 failure mode ("doesn't even count as 八股") was this gate missing.

Required artifacts before a draft can advance past the draft stage:

* ``paper/style_ref/EXEMPLAR.json`` (``exemplar_schema_version=2``)
  with ≥2 exemplars, each carrying ``title``, ``url``, ``venue``,
  ``year``, ``local_pdf`` (file present on disk), non-empty
  ``pdf_sha256``, and a ``structural_profile`` object that includes a
  figure inventory (``figure_inventory`` / ``figures`` /
  ``figure_table_inventory``). The figure inventory is the user's
  third explicit requirement: "分析他们具体做了哪些图，并按照分析结果
  来执行."
* ``paper/style_ref/STYLE_PROFILE.md`` ≥ 2000 chars (not a one-line
  stub). Captures top-conference formatting observations the agent
  must internalise.
* ``paper/style_ref/EXEMPLAR_SUITABILITY.json``:
  ``verdict=="PASS"`` + ``primary_exemplar`` matching one of the
  EXEMPLAR slugs + ``no_prose_copy_attestation==true``.
* ``paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md`` ≥ 1500 chars.

At submission stage only, additionally:

* ``paper/style_ref/STRUCTURE_CONFORMANCE.json`` with
  ``conformance_schema_version=1``, ``verdict=="PASS"``, and a non-empty
  ``section_mappings`` array — proves the final LaTeX section order
  was kept aligned with the blueprint.

**Format-conformance check** (added 2026-06-05):

  Pure-text exemplar grounding wasn't enough — v2 produced a passing
  STRUCTURE_CONFORMANCE.json while still leaving real format problems
  in the paper. The gate now also requires:

  * Each exemplar entry contains a ``format_facts`` object (or
    ``format_facts_path`` pointing at one) — produced by
    ``argus_skill.tools.format_facts``.
  * ``paper/PAPER_FORMAT_FACTS.json`` exists (the paper's own facts,
    same shape).
  * For draft+ stages: the paper's format facts are within tolerance of
    the primary exemplar's on ``total_pages`` / ``section_count`` /
    ``figure_count`` / ``table_count`` / ``citations_per_page`` /
    ``body_pages_before_references``. Tolerances are intentionally
    generous (see ``argus_skill.tools.format_facts.DEFAULT_TOLERANCES``);
    the goal is "ballpark same venue", not byte-identical.

This is structural / anti-fab. We do NOT score whether the exemplars
are "the right" exemplars or whether the style profile is "deep
enough" beyond a character floor — those are quality judgments and
belong to the reviewer.

CLI:
    python -m argus_skill.skills.exemplar_grounding --project-root .
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..tools.format_facts import (
    DEFAULT_TOLERANCES,
    diff_against_exemplar,
)

MIN_EXEMPLARS = 2
MIN_STYLE_PROFILE_CHARS = 2000
MIN_BLUEPRINT_CHARS = 1500
PAPER_FORMAT_FACTS_PATH = "paper/PAPER_FORMAT_FACTS.json"
FIGURE_INVENTORY_KEYS = (
    "figure_inventory",
    "figures",
    "figure_table_inventory",
    "figure_plan",
    "figures_and_tables",
)


@dataclass
class GroundingIssue:
    code: str
    detail: str


@dataclass
class GroundingReport:
    style_ref_dir: Path | None
    exemplar_count: int = 0
    exemplar_slugs: list[str] = field(default_factory=list)
    primary_exemplar: str | None = None
    style_profile_chars: int = 0
    blueprint_chars: int = 0
    has_conformance_json: bool = False
    conformance_section_mappings: int = 0
    paper_format_facts_present: bool = False
    format_diff_findings: list[dict] = field(default_factory=list)
    issues: list[GroundingIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_text(self) -> str:
        lines = []
        if not self.ok:
            lines.append(
                f"{len(self.issues)} exemplar-grounding violation(s); "
                "draft cannot advance without studying top-venue exemplars:"
            )
            for issue in self.issues:
                lines.append(f"  [{issue.code}] {issue.detail}")
            lines.append("")
        lines.append("Exemplar grounding state:")
        lines.append(
            f"  exemplars in EXEMPLAR.json: {self.exemplar_count} "
            f"({', '.join(self.exemplar_slugs) or '<none>'})"
        )
        lines.append(f"  primary_exemplar: {self.primary_exemplar or '<unset>'}")
        lines.append(f"  STYLE_PROFILE.md chars: {self.style_profile_chars}")
        lines.append(f"  PAPER_STRUCTURE_BLUEPRINT.md chars: {self.blueprint_chars}")
        lines.append(
            f"  STRUCTURE_CONFORMANCE.json present: {self.has_conformance_json}"
            f" ({self.conformance_section_mappings} section mapping(s))"
        )
        lines.append(
            f"  PAPER_FORMAT_FACTS.json present: {self.paper_format_facts_present}"
        )
        if self.format_diff_findings:
            lines.append("  format diff vs primary exemplar:")
            for f in self.format_diff_findings:
                mark = "ok" if f.get("within_tolerance") else "OFF"
                lines.append(
                    f"    [{mark}] {f.get('field')}: paper={f.get('paper_value')} "
                    f"exemplar={f.get('exemplar_value')} "
                    f"Δabs={f.get('delta_abs')} Δrel={f.get('delta_rel')}"
                )
        return "\n".join(lines)


def _exemplar_slug(entry: dict) -> str:
    """Best-effort slug for an exemplar — used to match primary_exemplar.

    Prefer an explicit ``slug``; fall back to the directory name of
    ``local_pdf`` (skills typically store at
    ``paper/style_ref/exemplars/<slug>/paper.pdf``).
    """
    raw = str(entry.get("slug") or "").strip()
    if raw:
        return raw
    local = str(entry.get("local_pdf") or "").strip()
    if local:
        p = Path(local)
        # parent of paper.pdf gives the slug dir
        return p.parent.name or p.stem
    return str(entry.get("title") or "").strip().lower().replace(" ", "-")[:32]


def _entry_has_figure_inventory(profile: dict) -> bool:
    if not isinstance(profile, dict):
        return False
    for key in FIGURE_INVENTORY_KEYS:
        val = profile.get(key)
        if val:  # non-empty list/dict/string is fine
            return True
    return False


def validate_exemplar_grounding(
    project_root: Path,
    *,
    require_conformance: bool = False,
) -> GroundingReport:
    """Validate the style_ref/ contract.

    ``require_conformance`` toggles whether STRUCTURE_CONFORMANCE.json is
    enforced. The skill makes it a post-draft artifact, so we only turn
    it on at the submission stage.
    """
    style_ref = project_root / "paper" / "style_ref"
    if not style_ref.is_dir():
        return GroundingReport(
            style_ref_dir=None,
            issues=[GroundingIssue(
                code="missing_style_ref_dir",
                detail=(
                    "paper/style_ref/ not found — run the paper-exemplar-"
                    "pdf-learning skill to fetch 2+ top-venue EMNLP/ACL "
                    "exemplars BEFORE drafting"
                ),
            )],
        )

    report = GroundingReport(style_ref_dir=style_ref)

    # 1. EXEMPLAR.json
    exemplar_path = style_ref / "EXEMPLAR.json"
    exemplars: list[dict] = []
    if not exemplar_path.exists():
        report.issues.append(GroundingIssue(
            code="missing_exemplar_json",
            detail=(
                "paper/style_ref/EXEMPLAR.json not found — list ≥2 "
                "downloaded top-venue exemplars with venue/year/local_pdf/"
                "pdf_sha256/structural_profile"
            ),
        ))
    else:
        try:
            data = json.loads(exemplar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.issues.append(GroundingIssue(
                code="malformed_exemplar_json",
                detail=f"EXEMPLAR.json is not valid JSON: {exc}",
            ))
            data = None
        if isinstance(data, dict):
            schema_version = data.get("exemplar_schema_version")
            if schema_version != 2:
                report.issues.append(GroundingIssue(
                    code="exemplar_schema_version_mismatch",
                    detail=(
                        f"exemplar_schema_version={schema_version!r} "
                        "(must be 2)"
                    ),
                ))
            raw = data.get("exemplars")
            if isinstance(raw, list):
                exemplars = [e for e in raw if isinstance(e, dict)]
            else:
                report.issues.append(GroundingIssue(
                    code="exemplar_json_missing_exemplars_array",
                    detail="EXEMPLAR.json must have an 'exemplars' array",
                ))

    report.exemplar_count = len(exemplars)
    if exemplars and report.exemplar_count < MIN_EXEMPLARS:
        report.issues.append(GroundingIssue(
            code="too_few_exemplars",
            detail=(
                f"only {report.exemplar_count} exemplar(s) "
                f"(minimum {MIN_EXEMPLARS}); EMNLP/ACL drafts need at "
                "least one recent best/outstanding paper plus one same-"
                "direction paper"
            ),
        ))

    for idx, e in enumerate(exemplars):
        slug = _exemplar_slug(e)
        report.exemplar_slugs.append(slug)
        title = str(e.get("title") or "").strip()
        venue = str(e.get("venue") or "").strip()
        year = e.get("year")
        local_pdf = str(e.get("local_pdf") or "").strip()
        sha = str(e.get("pdf_sha256") or "").strip()
        prof = e.get("structural_profile")
        if not title:
            report.issues.append(GroundingIssue(
                code="exemplar_missing_title",
                detail=f"exemplars[{idx}] (slug={slug!r}) has no title",
            ))
        if not venue or not year:
            report.issues.append(GroundingIssue(
                code="exemplar_missing_venue_or_year",
                detail=(
                    f"exemplars[{idx}] (slug={slug!r}) needs venue + year "
                    "(top-venue check)"
                ),
            ))
        # Anti-fab: local_pdf must actually be on disk
        if not local_pdf:
            report.issues.append(GroundingIssue(
                code="exemplar_missing_local_pdf",
                detail=(
                    f"exemplars[{idx}] (slug={slug!r}) has no local_pdf "
                    "path; the actual PDF must be downloaded, not just URL"
                ),
            ))
        else:
            candidates = [
                project_root / local_pdf,
                Path(local_pdf),
            ]
            if not any(p.exists() for p in candidates):
                report.issues.append(GroundingIssue(
                    code="exemplar_local_pdf_missing_on_disk",
                    detail=(
                        f"exemplars[{idx}] local_pdf={local_pdf!r} does "
                        "not exist; the PDF must be physically downloaded"
                    ),
                ))
        if not sha:
            report.issues.append(GroundingIssue(
                code="exemplar_missing_pdf_sha256",
                detail=(
                    f"exemplars[{idx}] (slug={slug!r}) has no pdf_sha256; "
                    "required to prove the local PDF was hashed, not faked"
                ),
            ))
        # User's requirement #3: every exemplar must list what figures it
        # has, so the agent can mirror the figure plan.
        if not _entry_has_figure_inventory(prof if isinstance(prof, dict) else {}):
            report.issues.append(GroundingIssue(
                code="exemplar_missing_figure_inventory",
                detail=(
                    f"exemplars[{idx}] (slug={slug!r}) structural_profile "
                    f"has no figure inventory (one of {list(FIGURE_INVENTORY_KEYS)}); "
                    "every exemplar must record what figures/tables it "
                    "uses so this paper's figure plan can mirror it"
                ),
            ))

    # 2. STYLE_PROFILE.md
    style_profile = style_ref / "STYLE_PROFILE.md"
    if not style_profile.exists():
        report.issues.append(GroundingIssue(
            code="missing_style_profile_md",
            detail=(
                "STYLE_PROFILE.md not found — write a thick structural "
                "profile (abstract shape, section/page allocation, "
                "figure/table inventory, related-work shape, evaluation "
                "layout, formatting/layout lessons)"
            ),
        ))
    else:
        try:
            text = style_profile.read_text(encoding="utf-8")
        except OSError:
            text = ""
        report.style_profile_chars = len(text)
        if report.style_profile_chars < MIN_STYLE_PROFILE_CHARS:
            report.issues.append(GroundingIssue(
                code="style_profile_too_short",
                detail=(
                    f"STYLE_PROFILE.md is only {report.style_profile_chars} "
                    f"chars (minimum {MIN_STYLE_PROFILE_CHARS}); a one-line "
                    "stub is not a structural profile"
                ),
            ))

    # 3. EXEMPLAR_SUITABILITY.json
    suit_path = style_ref / "EXEMPLAR_SUITABILITY.json"
    if not suit_path.exists():
        report.issues.append(GroundingIssue(
            code="missing_exemplar_suitability_json",
            detail=(
                "EXEMPLAR_SUITABILITY.json not found — required pre-draft "
                "suitability lock with verdict=PASS, primary_exemplar slug, "
                "and no_prose_copy_attestation=true"
            ),
        ))
    else:
        try:
            sdata = json.loads(suit_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.issues.append(GroundingIssue(
                code="malformed_exemplar_suitability_json",
                detail=f"EXEMPLAR_SUITABILITY.json: {exc}",
            ))
            sdata = None
        if isinstance(sdata, dict):
            verdict = str(sdata.get("verdict") or "").strip().upper()
            if verdict != "PASS":
                report.issues.append(GroundingIssue(
                    code="exemplar_suitability_not_pass",
                    detail=(
                        f"EXEMPLAR_SUITABILITY.verdict={verdict!r} (must be "
                        "PASS); if no candidate fits, fetch a better "
                        "exemplar instead of drafting from a bad template"
                    ),
                ))
            if not bool(sdata.get("no_prose_copy_attestation")):
                report.issues.append(GroundingIssue(
                    code="exemplar_suitability_no_prose_copy_attestation_missing",
                    detail=(
                        "no_prose_copy_attestation must be true; structural "
                        "guidance only, never copy prose from exemplars"
                    ),
                ))
            primary = str(sdata.get("primary_exemplar") or "").strip()
            report.primary_exemplar = primary or None
            if not primary:
                report.issues.append(GroundingIssue(
                    code="exemplar_suitability_no_primary",
                    detail="primary_exemplar is empty",
                ))
            elif report.exemplar_slugs and primary not in report.exemplar_slugs:
                report.issues.append(GroundingIssue(
                    code="primary_exemplar_unknown_slug",
                    detail=(
                        f"primary_exemplar={primary!r} not in EXEMPLAR.json "
                        f"slugs ({report.exemplar_slugs})"
                    ),
                ))

    # 4. PAPER_STRUCTURE_BLUEPRINT.md
    blueprint = style_ref / "PAPER_STRUCTURE_BLUEPRINT.md"
    if not blueprint.exists():
        report.issues.append(GroundingIssue(
            code="missing_paper_structure_blueprint_md",
            detail=(
                "PAPER_STRUCTURE_BLUEPRINT.md not found — turn the primary "
                "exemplar into this paper's section order, page budget, "
                "paragraph roles, figure/table plan, related-work grouping, "
                "and evaluation sequence BEFORE writing prose"
            ),
        ))
    else:
        try:
            btext = blueprint.read_text(encoding="utf-8")
        except OSError:
            btext = ""
        report.blueprint_chars = len(btext)
        if report.blueprint_chars < MIN_BLUEPRINT_CHARS:
            report.issues.append(GroundingIssue(
                code="paper_structure_blueprint_too_short",
                detail=(
                    f"PAPER_STRUCTURE_BLUEPRINT.md is only "
                    f"{report.blueprint_chars} chars (minimum "
                    f"{MIN_BLUEPRINT_CHARS}); a real blueprint covers "
                    "section order, page budget, and paragraph roles"
                ),
            ))

    # 5. STRUCTURE_CONFORMANCE.json — only at submission
    conf_path = style_ref / "STRUCTURE_CONFORMANCE.json"
    if conf_path.exists():
        report.has_conformance_json = True
        try:
            cdata = json.loads(conf_path.read_text(encoding="utf-8"))
            if isinstance(cdata, dict):
                mappings = cdata.get("section_mappings") or []
                if isinstance(mappings, list):
                    report.conformance_section_mappings = len(mappings)
        except (OSError, json.JSONDecodeError):
            pass

    if require_conformance:
        if not conf_path.exists():
            report.issues.append(GroundingIssue(
                code="missing_structure_conformance_json",
                detail=(
                    "STRUCTURE_CONFORMANCE.json required at submission — "
                    "map every final top-level section to an exemplar phase"
                ),
            ))
        else:
            try:
                cdata = json.loads(conf_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                report.issues.append(GroundingIssue(
                    code="malformed_structure_conformance_json",
                    detail=f"STRUCTURE_CONFORMANCE.json: {exc}",
                ))
                cdata = None
            if isinstance(cdata, dict):
                if cdata.get("conformance_schema_version") != 1:
                    report.issues.append(GroundingIssue(
                        code="structure_conformance_schema_version_mismatch",
                        detail="conformance_schema_version must be 1",
                    ))
                if str(cdata.get("verdict") or "").upper() != "PASS":
                    report.issues.append(GroundingIssue(
                        code="structure_conformance_not_pass",
                        detail=(
                            f"STRUCTURE_CONFORMANCE.verdict="
                            f"{cdata.get('verdict')!r} (must be PASS)"
                        ),
                    ))
                if report.conformance_section_mappings == 0:
                    report.issues.append(GroundingIssue(
                        code="structure_conformance_empty_section_mappings",
                        detail=(
                            "section_mappings is empty; every final top-"
                            "level section before references/appendix must "
                            "map to an exemplar phase"
                        ),
                    ))

    # ------------------------------------------------------------------
    # Format-facts conformance: each exemplar must have format_facts,
    # and the paper's own format facts must be in the same ballpark as
    # the primary exemplar's. This is what catches "PDF compiled clean
    # but doesn't look like a same-venue paper" — the gap text-only
    # exemplar grounding kept missing.
    # ------------------------------------------------------------------
    primary_facts: dict | None = None
    for entry in exemplars:
        slug = _exemplar_slug(entry)
        facts = _load_exemplar_format_facts(entry, project_root)
        if facts is None:
            report.issues.append(GroundingIssue(
                code="exemplar_missing_format_facts",
                detail=(
                    f"exemplar {slug!r} has no format_facts (inline) and "
                    "no format_facts_path; run "
                    "`python -m argus_skill.tools.format_facts <local_pdf> "
                    "--write paper/style_ref/exemplars/<slug>/format_facts.json` "
                    "and reference it from EXEMPLAR.json"
                ),
            ))
            continue
        if report.primary_exemplar and slug == report.primary_exemplar:
            primary_facts = facts

    paper_facts_path = project_root / PAPER_FORMAT_FACTS_PATH
    if not paper_facts_path.exists():
        report.issues.append(GroundingIssue(
            code="missing_paper_format_facts",
            detail=(
                f"{PAPER_FORMAT_FACTS_PATH} not found — run "
                "`python -m argus_skill.tools.format_facts paper/main.pdf "
                f"--write {PAPER_FORMAT_FACTS_PATH}` after every PDF rebuild"
            ),
        ))
    else:
        try:
            paper_facts = _normalize_facts(
                json.loads(paper_facts_path.read_text(encoding="utf-8"))
            )
            report.paper_format_facts_present = True
        except (OSError, json.JSONDecodeError) as exc:
            report.issues.append(GroundingIssue(
                code="malformed_paper_format_facts",
                detail=f"{PAPER_FORMAT_FACTS_PATH}: {exc}",
            ))
            paper_facts = None

        if paper_facts is not None and primary_facts is not None:
            findings = diff_against_exemplar(
                paper_facts, primary_facts, DEFAULT_TOLERANCES,
            )
            report.format_diff_findings = [
                {
                    "field": f.field,
                    "paper_value": f.paper_value,
                    "exemplar_value": f.exemplar_value,
                    "delta_abs": f.delta_abs,
                    "delta_rel": f.delta_rel,
                    "within_tolerance": f.within_tolerance,
                }
                for f in findings
            ]
            offenders = [f for f in findings if not f.within_tolerance]
            if offenders:
                spans = "; ".join(
                    f"{f.field}: paper={f.paper_value} vs "
                    f"exemplar={f.exemplar_value} (Δrel={f.delta_rel})"
                    for f in offenders
                )
                report.issues.append(GroundingIssue(
                    code="format_facts_diverge_from_primary_exemplar",
                    detail=(
                        f"{len(offenders)} format field(s) outside tolerance "
                        f"of primary exemplar {report.primary_exemplar!r}: "
                        f"{spans}. Tolerances are intentionally loose; "
                        "exceeding them means the paper does not look like a "
                        "same-venue paper. Fix by adjusting section lengths, "
                        "figure/table count, or citation density to mirror "
                        "the exemplar."
                    ),
                ))

    return report


def _load_exemplar_format_facts(entry: dict, project_root: Path) -> dict | None:
    """Read an exemplar's format_facts, either inline or from a sidecar
    file. Returns None when neither is present. Output is always the
    flat-shape dict the diff helper expects (see ``_normalize_facts``)."""
    inline = entry.get("format_facts")
    if isinstance(inline, dict) and inline:
        return _normalize_facts(inline)
    path_field = entry.get("format_facts_path")
    if not path_field:
        return None
    candidates = [Path(path_field)]
    if not candidates[0].is_absolute():
        candidates.insert(0, project_root / path_field)
    for c in candidates:
        if c.exists():
            try:
                return _normalize_facts(
                    json.loads(c.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError):
                return None
    return None


def _normalize_facts(raw: dict) -> dict:
    """Flatten format-facts payload to the shape diff_against_exemplar
    expects.

    Accepts three shapes:

    1. **Flat** (what ``argus_skill.tools.format_facts`` emits): ``raw``
       already has ``total_pages``/``figure_count``/etc at the top level.
    2. **Tool-wrapped**: ``raw['tool_output']`` carries the flat dict.
       Agent skills wrap the raw tool output when they want to attach
       sidecar metadata (manual audits, notes).
    3. **Manual override**: ``raw['manual_page_audit']`` carries
       human-corrected values for fields the regex extractor got wrong
       (typical for unusual ACL/EMNLP PDFs where ``References`` doesn't
       land on its own line). Manual values win — that's exactly why
       the agent recorded them.

    Returned dict is the union: tool output as base, manual_page_audit
    overrides on top, with any inline top-level keys taking precedence
    over both.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    tool_output = raw.get("tool_output")
    if isinstance(tool_output, dict):
        out.update(tool_output)
    manual = raw.get("manual_page_audit")
    if isinstance(manual, dict):
        # Only carry over numeric/quantitative keys; ignore audit
        # metadata flags like ``authoritative_for_acl_page_boundaries``.
        for k, v in manual.items():
            if isinstance(v, (int, float)) and v is not None:
                out[k] = v
    # Top-level numeric keys take final precedence (covers shape 1 and
    # also lets the agent surface a corrected value at the top level).
    for k, v in raw.items():
        if k in ("tool_output", "manual_page_audit", "note", "source"):
            continue
        if isinstance(v, (int, float, str, bool, list)) and v is not None:
            out.setdefault(k, v)
            # numeric overrides:
            if isinstance(v, (int, float)):
                out[k] = v
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument(
        "--require-conformance", action="store_true",
        help="also enforce STRUCTURE_CONFORMANCE.json (submission stage)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = validate_exemplar_grounding(
        args.project_root.resolve(),
        require_conformance=args.require_conformance,
    )
    if args.json:
        payload = {
            "ok": report.ok,
            "exemplar_count": report.exemplar_count,
            "exemplar_slugs": report.exemplar_slugs,
            "primary_exemplar": report.primary_exemplar,
            "style_profile_chars": report.style_profile_chars,
            "blueprint_chars": report.blueprint_chars,
            "has_conformance_json": report.has_conformance_json,
            "conformance_section_mappings": report.conformance_section_mappings,
            "issues": [
                {"code": i.code, "detail": i.detail} for i in report.issues
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(report.to_text())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
