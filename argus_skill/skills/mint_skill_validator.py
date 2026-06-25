"""Held-out fixture validator for mint-skill candidates.

Implements the **execution gate** of the self-evolve loop. Per SkillLens
(arXiv 2605.23899): LLM judges are 46.4% worse than chance at telling
effective skills from ineffective ones by **reading** them. So this
validator never reads the candidate skill text — it executes the
candidate's attached script against held-out (input, expected) fixtures
and reports pass/fail per case.

Designed to be invoked as a ``check_command`` so the standard reviewer
flow (CheckResult.passed → D1 coerce → reviewer prompt) handles the
verdict path automatically.

Skill layout this validator expects (the ``mint-skill`` prompt enforces
the same shape when writing candidates):

    argus_builtin_skills/engineer/
      ├── <slug>.md                                       # skill prompt
      └── <slug>_scripts/
          └── main.py                                     # executable

    .argus-fixtures/<slug>/
      ├── case_001/
      │   ├── input.<ext>                                 # piped to stdin
      │   └── expected.<ext>                              # diffed vs stdout
      ├── case_002/...
      └── case_003/...

The validator runs::

    python <skill_scripts>/main.py < case_NNN/input.<ext>

and compares stdout to ``expected.<ext>`` (byte-exact for text formats,
JSON-equal for ``.json``). At least :data:`MIN_FIXTURE_CASES` cases are
required, otherwise the gate fails for "insufficient evidence" — that
threshold is a structural minimum (not a quality call) per skill 04.

CLI exit code: 0 if every fixture passes, 1 otherwise. The harness
exit-code semantics integrate cleanly with stage_check via D1.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

MIN_FIXTURE_CASES = 3
DEFAULT_TIMEOUT_SECONDS = 60
SUPPORTED_JSON_EXTS = (".json",)


@dataclass
class FixtureCase:
    name: str
    input_path: Path
    expected_path: Path

    @property
    def ext(self) -> str:
        return self.expected_path.suffix.lower()


@dataclass
class CaseResult:
    case: str
    passed: bool
    detail: str
    actual_excerpt: str = ""
    expected_excerpt: str = ""


@dataclass
class ValidatorReport:
    skill_slug: str
    script_path: Path
    fixtures_root: Path
    cases: list[CaseResult] = field(default_factory=list)
    structural_errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def ok(self) -> bool:
        return (
            not self.structural_errors
            and self.total >= MIN_FIXTURE_CASES
            and self.passed_count == self.total
        )

    def to_dict(self) -> dict:
        return {
            "skill_slug": self.skill_slug,
            "script_path": str(self.script_path),
            "fixtures_root": str(self.fixtures_root),
            "min_fixture_cases": MIN_FIXTURE_CASES,
            "total": self.total,
            "passed": self.passed_count,
            "ok": self.ok,
            "structural_errors": list(self.structural_errors),
            "cases": [
                {
                    "case": c.case,
                    "passed": c.passed,
                    "detail": c.detail,
                    "actual_excerpt": c.actual_excerpt,
                    "expected_excerpt": c.expected_excerpt,
                }
                for c in self.cases
            ],
        }


def _find_fixture_cases(fixtures_root: Path) -> tuple[list[FixtureCase], list[str]]:
    cases: list[FixtureCase] = []
    errors: list[str] = []
    if not fixtures_root.is_dir():
        errors.append(f"fixtures root {fixtures_root} does not exist")
        return (cases, errors)
    for case_dir in sorted(fixtures_root.iterdir()):
        if not case_dir.is_dir() or case_dir.name.startswith((".", "_")):
            continue
        inputs = sorted(case_dir.glob("input.*"))
        expecteds = sorted(case_dir.glob("expected.*"))
        if len(inputs) != 1 or len(expecteds) != 1:
            errors.append(
                f"{case_dir.name}: need exactly one input.* and one expected.*, "
                f"got input={[p.name for p in inputs]} expected={[p.name for p in expecteds]}"
            )
            continue
        cases.append(
            FixtureCase(
                name=case_dir.name,
                input_path=inputs[0],
                expected_path=expecteds[0],
            )
        )
    return (cases, errors)


def _compare_text(actual: str, expected: str) -> tuple[bool, str]:
    if actual == expected:
        return (True, "byte-exact match")
    # Tolerant trailing-newline-only difference (very common).
    if actual.rstrip("\n") == expected.rstrip("\n"):
        return (True, "match modulo trailing newline")
    return (False, "output differs from expected (use --json to see excerpts)")


def _compare_json(actual: str, expected: str) -> tuple[bool, str]:
    try:
        a = json.loads(actual)
    except json.JSONDecodeError as exc:
        return (False, f"actual output is not valid JSON: {exc}")
    try:
        e = json.loads(expected)
    except json.JSONDecodeError as exc:
        return (False, f"expected file is not valid JSON: {exc}")
    if a == e:
        return (True, "json-equal")
    return (False, "json structures differ (use --json to see excerpts)")


def _excerpt(text: str, max_chars: int = 400) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"... (+{len(text) - max_chars} chars)"


def _run_case(
    script_path: Path,
    case: FixtureCase,
    *,
    python_exe: str,
    timeout: int,
    extra_env: dict[str, str] | None = None,
) -> CaseResult:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.run(
            [python_exe, str(script_path)],
            stdin=case.input_path.open("rb"),
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return CaseResult(
            case=case.name, passed=False,
            detail=f"timed out after {timeout}s",
        )
    except OSError as exc:
        return CaseResult(
            case=case.name, passed=False,
            detail=f"failed to invoke script: {exc}",
        )

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        return CaseResult(
            case=case.name, passed=False,
            detail=f"script exited {proc.returncode}; stderr={_excerpt(stderr)}",
        )

    actual = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    expected = case.expected_path.read_text(encoding="utf-8")

    if case.ext in SUPPORTED_JSON_EXTS:
        ok, detail = _compare_json(actual, expected)
    else:
        ok, detail = _compare_text(actual, expected)

    return CaseResult(
        case=case.name, passed=ok, detail=detail,
        actual_excerpt=_excerpt(actual) if not ok else "",
        expected_excerpt=_excerpt(expected) if not ok else "",
    )


def _default_script_path(skill_md: Path) -> Path | None:
    # Conventional layout: <stem>.md  +  <stem>_scripts/main.py
    stem = skill_md.stem
    candidate = skill_md.parent / f"{stem}_scripts" / "main.py"
    if candidate.is_file():
        return candidate
    return None


def validate_candidate_skill(
    skill_md: Path,
    fixtures_root: Path,
    *,
    script_path: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    python_exe: str | None = None,
) -> ValidatorReport:
    skill_md = Path(skill_md)
    fixtures_root = Path(fixtures_root)
    if script_path is None:
        script_path = _default_script_path(skill_md)
    python_exe = python_exe or sys.executable

    report = ValidatorReport(
        skill_slug=skill_md.stem,
        script_path=Path(script_path) if script_path else Path("<missing>"),
        fixtures_root=fixtures_root,
    )

    if not skill_md.is_file():
        report.structural_errors.append(f"skill markdown not found: {skill_md}")
        return report
    if script_path is None or not Path(script_path).is_file():
        report.structural_errors.append(
            f"script not found; expected at {skill_md.parent / (skill_md.stem + '_scripts/main.py')}"
        )
        return report

    cases, fixture_errors = _find_fixture_cases(fixtures_root)
    report.structural_errors.extend(fixture_errors)
    if len(cases) < MIN_FIXTURE_CASES:
        report.structural_errors.append(
            f"need at least {MIN_FIXTURE_CASES} fixture cases, found {len(cases)}; "
            f"mint-skill prompt requires writing 3+ held-out cases"
        )
        return report

    for case in cases:
        report.cases.append(
            _run_case(
                Path(script_path), case,
                python_exe=python_exe, timeout=timeout,
            )
        )

    return report


def _print_text_report(report: ValidatorReport) -> None:
    print(f"mint_skill_validator: skill={report.skill_slug}")
    print(f"  script    : {report.script_path}")
    print(f"  fixtures  : {report.fixtures_root}")
    if report.structural_errors:
        print(f"STRUCTURAL FAIL ({len(report.structural_errors)}):")
        for e in report.structural_errors:
            print(f"  - {e}")
        return
    status = "OK" if report.ok else "FAIL"
    print(f"{status} — {report.passed_count}/{report.total} cases passed "
          f"(min required: {MIN_FIXTURE_CASES})")
    for c in report.cases:
        mark = "✅" if c.passed else "❌"
        print(f"  {mark} {c.case}: {c.detail}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", type=Path, required=True,
                        help="path to candidate skill markdown")
    parser.add_argument("--fixtures", type=Path, required=True,
                        help="path to fixtures root directory")
    parser.add_argument("--script", type=Path, default=None,
                        help="explicit script path (default: <stem>_scripts/main.py)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--python", type=str, default=None,
                        help="python interpreter to use (default: sys.executable)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = validate_candidate_skill(
        args.skill,
        args.fixtures,
        script_path=args.script,
        timeout=args.timeout,
        python_exe=args.python,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_text_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
