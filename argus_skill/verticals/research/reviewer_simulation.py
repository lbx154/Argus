"""Reviewer-simulation structural gate.

Forces the paper-review-revision-loop skill to produce a concrete,
machine-readable list of "what a hostile reviewer will ask" with each
question paired to where the paper now addresses it. Anti-fab structural
check (same class as ``evidence_chain`` and ``paper_structural_minimums``):
without this artifact, the agent can claim "I reviewed the draft" without
having actually pre-empted any reviewer objection.

Contract — ``paper/REVIEWER_QUESTIONS.json``:

    {
      "schema_version": 1,
      "generated_at": "2026-06-03T12:00:00Z",
      "questions": [
        {
          "id": "Q1",
          "question": "Why is the baseline only random-prompt, not SOTA?",
          "severity": "critical" | "major" | "minor",
          "addressed_in_section": "5.2 Baselines" | "limitations" | "appendix B",
          "addressed_evidence": "Table 3 shows SOTA reproduction at row 4."
        },
        ...
      ]
    }

Gate fires when any of these hold:

* the file is missing
* fewer than ``MIN_QUESTIONS`` items (default 10 — reviewer would ask
  more than that on a real submission; floor is venue-minimum)
* any item missing a required field, or with ``addressed_in_section``
  empty (reviewer questions that aren't addressed are open blockers,
  not "reviewed")
* the file's mtime is older than ``paper/main.tex`` (the question set
  was generated against an earlier draft and is stale)

This is a venue-floor anti-fab gate, not a quality judgment: it does not
score "are these the RIGHT questions" — the reviewer agent owns that
call. We only enforce that the list exists, is non-trivial, and was
refreshed against the current draft.

CLI:
    python -m argus_skill.verticals.research.reviewer_simulation --project-root .
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from ...skills.venue_profiles import resolve_venue_profile

MIN_QUESTIONS = 10
ALLOWED_SEVERITIES = frozenset({"critical", "major", "minor"})
QUESTIONS_FILENAME = "REVIEWER_QUESTIONS.json"


@dataclass
class SimulationIssue:
    code: str
    detail: str


@dataclass
class SimulationReport:
    questions_path: Path | None
    questions_found: int = 0
    addressed_count: int = 0
    severities: dict[str, int] = field(default_factory=dict)
    stale_vs_main_tex: bool = False
    issues: list[SimulationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_text(self) -> str:
        lines = []
        if not self.ok:
            lines.append(
                f"{len(self.issues)} reviewer-simulation contract violation(s); "
                f"draft cannot advance to review/submission until fixed:"
            )
            for issue in self.issues:
                lines.append(f"  [{issue.code}] {issue.detail}")
            lines.append("")
        lines.append("Reviewer-simulation counts:")
        lines.append(f"  questions in REVIEWER_QUESTIONS.json: {self.questions_found}")
        lines.append(f"  questions with addressed_in_section: {self.addressed_count}")
        if self.severities:
            sev_summary = ", ".join(
                f"{k}={v}" for k, v in sorted(self.severities.items())
            )
            lines.append(f"  severity breakdown: {sev_summary}")
        lines.append(f"  stale vs main.tex: {self.stale_vs_main_tex}")
        return "\n".join(lines)


def _find_questions(project_root: Path) -> Path | None:
    candidates = [
        project_root / "paper" / QUESTIONS_FILENAME,
        project_root / "paper" / "submission" / QUESTIONS_FILENAME,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _find_main_tex(project_root: Path) -> Path | None:
    for c in (
        project_root / "paper" / "main.tex",
        project_root / "paper" / "submission" / "main.tex",
    ):
        if c.exists():
            return c
    return None


def validate_reviewer_simulation(project_root: Path) -> SimulationReport:
    venue = resolve_venue_profile(project_root)
    qpath = _find_questions(project_root)
    if qpath is None:
        return SimulationReport(
            questions_path=None,
            issues=[SimulationIssue(
                code="missing_reviewer_questions",
                detail=(
                    f"paper/{QUESTIONS_FILENAME} not found — run the "
                    "paper-review-revision-loop or kill-argument skill to "
                    "produce a structured reviewer-question list before "
                    "advancing past draft"
                ),
            )],
        )

    report = SimulationReport(questions_path=qpath)
    try:
        data = json.loads(qpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.issues.append(SimulationIssue(
            code="malformed_reviewer_questions",
            detail=f"{qpath.name} is not valid JSON: {exc}",
        ))
        return report

    questions = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(questions, list):
        report.issues.append(SimulationIssue(
            code="malformed_reviewer_questions",
            detail=(
                f"{qpath.name} must be a JSON object with a top-level "
                "'questions' array"
            ),
        ))
        return report

    seen_ids: set[str] = set()
    for idx, q in enumerate(questions):
        if not isinstance(q, dict):
            report.issues.append(SimulationIssue(
                code="malformed_question_entry",
                detail=f"questions[{idx}] is not an object",
            ))
            continue
        qid = str(q.get("id") or "").strip()
        text = str(q.get("question") or "").strip()
        severity = str(q.get("severity") or "").strip().lower()
        addressed = str(q.get("addressed_in_section") or "").strip()
        if not text:
            report.issues.append(SimulationIssue(
                code="empty_question_text",
                detail=f"questions[{idx}] has no non-empty 'question' field",
            ))
            continue
        if severity not in ALLOWED_SEVERITIES:
            report.issues.append(SimulationIssue(
                code="invalid_severity",
                detail=(
                    f"questions[{idx}] severity={severity!r} not in "
                    f"{sorted(ALLOWED_SEVERITIES)}"
                ),
            ))
        if qid:
            if qid in seen_ids:
                report.issues.append(SimulationIssue(
                    code="duplicate_question_id",
                    detail=f"id {qid!r} appears more than once",
                ))
            seen_ids.add(qid)
        report.questions_found += 1
        if addressed:
            report.addressed_count += 1
        if severity in ALLOWED_SEVERITIES:
            report.severities[severity] = report.severities.get(severity, 0) + 1

    if report.questions_found < MIN_QUESTIONS:
        report.issues.append(SimulationIssue(
            code="too_few_questions",
            detail=(
                f"only {report.questions_found} reviewer question(s) "
                f"(minimum {MIN_QUESTIONS}); a real {venue.reviewer_persona} reviewer would "
                "raise more than that — see kill-argument and paper-"
                "review-revision-loop skills for elicitation playbooks"
            ),
        ))

    if report.questions_found and report.addressed_count < report.questions_found:
        missing = report.questions_found - report.addressed_count
        report.issues.append(SimulationIssue(
            code="unaddressed_reviewer_questions",
            detail=(
                f"{missing} of {report.questions_found} reviewer question(s) "
                "have empty 'addressed_in_section' — each open question is "
                "a reviewer-facing blocker; either patch the paper or move "
                "the question to limitations and cite that section"
            ),
        ))

    main_tex = _find_main_tex(project_root)
    if main_tex is not None:
        try:
            q_mtime = qpath.stat().st_mtime
            tex_mtime = main_tex.stat().st_mtime
            if q_mtime + 1e-3 < tex_mtime:  # small slack for FS rounding
                report.stale_vs_main_tex = True
                report.issues.append(SimulationIssue(
                    code="reviewer_questions_stale_vs_main_tex",
                    detail=(
                        f"{qpath.name} mtime ({q_mtime:.0f}) is older than "
                        f"paper/main.tex ({tex_mtime:.0f}); regenerate the "
                        "question set against the current draft"
                    ),
                ))
        except OSError:
            pass

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = validate_reviewer_simulation(args.project_root.resolve())
    if args.json:
        payload = {
            "ok": report.ok,
            "questions_path": (
                str(report.questions_path) if report.questions_path else None
            ),
            "questions_found": report.questions_found,
            "addressed_count": report.addressed_count,
            "severities": report.severities,
            "stale_vs_main_tex": report.stale_vs_main_tex,
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
