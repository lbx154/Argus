"""Lean evidence as runtime state, not paperwork.

``argus_skill.tools.lean_check`` can already compile one Lean file and say,
fail-closed, whether it proved anything. Nothing in this vertical read that
answer, so a project could carry a ``.lean`` file with a ``sorry`` in it and
still complete: the only mechanical gate was whether a JSON file existed.

This module wires the answer into completion. It draws one distinction that
Lean itself cannot:

**Proof validity** is whether the formal statement you wrote down was actually
proved. Lean decides that.

**Statement fidelity** is whether that formal statement says the same thing as
the mathematics you claim to have settled. Lean cannot decide that, and a
compiling proof of a mistranslated statement is the most expensive kind of
wrong answer this vertical can produce. So a Lean source without a separate,
substantive fidelity document is treated as unfalsifiable rather than as
evidence — the same separation ``prepare_canonical_lean_artifacts`` already
enforces by refusing to let the two be one file.

The governing rule is narrow and absolute:

    Formalizing is optional. Having formalized is a promise, and every promise
    here must be redeemable on demand.

So a project with no ``.lean`` file gets no issues and pays no import cost;
``lean_check`` is not even loaded. But once a ``.lean`` file exists, it must be
able to show a current, complete, hash-bound compiler result that says the
proof went through. Anything else blocks — including the cases where the
compiler never reached the mathematics at all.

That last point is the one worth being explicit about, because an earlier
version of this module got it wrong. A compile that failed because the host has
no Mathlib genuinely says nothing about the mathematics, and it is still
reported as such — ``lean_unverified_missing_dependency`` reads differently from
``lean_compile_failed``, so a reviewer can tell an environment gap from a broken
proof. What does *not* follow is that it should pass. Serious formalization
imports Mathlib; a host without Mathlib fails every such compile; excusing those
failures would switch the gate off in precisely the case it exists for. The
distinction survives in the message. It does not buy passage.

The escape hatch for a machine without a toolchain is not a lenient gate, it is
the optional step: do not commit a ``.lean`` file you cannot verify.

The gate never compiles. It reads recorded evidence, so a completion decision
costs a few file reads even when the project carries a proof that takes minutes
to build. Producing that evidence is the Engineer's step, through ``verify``
below — or, when the compile is long enough that waiting it out is the expensive
part, through the ``submit``/``reclaim`` pair in ``lean_async``, which records
the same thing on the same terms.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "LEAN_DIR_RELPATH",
    "MAX_DISCOVERED_SOURCES",
    "CompiledArtifactChangedError",
    "LeanEvidenceReport",
    "LeanIssue",
    "LeanSourceEvidence",
    "classify_environment_failure",
    "discover_lean_sources",
    "lean_evidence_issues",
    "main",
    "source_evidence",
    "validate_lean_evidence",
    "verify_lean_source",
]

#: Where ``verify`` puts canonical artifacts when no directory is given. Any
#: other location works too; discovery is by file extension, not by path.
LEAN_DIR_RELPATH = ("research", "lean")

#: Directories that cannot contain project-authored mathematics. Deliberately
#: short: every name here is a hiding place, so only unambiguous ones qualify.
#: ``build/``, ``Mathlib/`` and dot-directories are *not* here — an earlier
#: version skipped them, which made "put the proof in build/" equivalent to
#: "have no proof". A genuinely vendored library outside ``.lake`` will trip
#: the discovery ceiling instead, which reports rather than hides.
_VENDOR_DIR_NAMES = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".lake",
    "lake-packages",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
})

#: A project that trips this is pointing at a checkout rather than at its own
#: work. Hitting it blocks: silently checking the first N of an unknown number
#: of proofs is indistinguishable from checking none of them.
MAX_DISCOVERED_SOURCES = 64

#: Failures where the compiler never reached the mathematics. Matched against
#: lowercased ``stdout + stderr``. Observed shape on a Lean-without-Mathlib
#: box: ``error: unknown module prefix 'Mathlib'`` followed by ``No directory
#: 'Mathlib' or file 'Mathlib.olean' in the search path entries:``.
#:
#: This only selects which blocking message to emit. Misclassifying here costs
#: a confusing sentence, never a pass.
_MISSING_DEPENDENCY_PATTERNS = (
    "unknown module prefix",
    "in the search path entries",
    ".olean' in the search path",
    "unknown package",
    "could not find native implementation",
)

_TOOLCHAIN_ABSENT_STATUS = "unavailable"

_KNOWN_STATUSES = frozenset({
    "success",
    "proof_hole",
    "syntax_error",
    "type_error",
    "timeout",
    _TOOLCHAIN_ABSENT_STATUS,
})

#: Named declarations a fidelity document should be able to point at.
_DECLARATION = re.compile(
    r"^[ \t]*(?:@\[[^\]]*\][ \t]*)?"
    r"(?:private[ \t]+|protected[ \t]+|noncomputable[ \t]+|unsafe[ \t]+"
    r"|partial[ \t]+|scoped[ \t]+|local[ \t]+)*"
    r"(?:theorem|lemma|def|abbrev|instance|structure|inductive)[ \t]+"
    r"(«[^»\n]+»|[A-Za-z_][A-Za-z0-9_'!?]*(?:\.[A-Za-z_][A-Za-z0-9_'!?]*)*)",
    re.MULTILINE,
)

#: Characters that continue a Lean identifier, for boundary-anchored matching.
_IDENTIFIER_CHARS = r"A-Za-z0-9_'.!?"

_STATEMENT_FIDELITY_NAME = "statement_fidelity.md"
_LEAN_CHECK_RESULT_NAME = "lean_check.json"

#: A fidelity document that is only a heading describes nothing. Counted over
#: non-blank lines with heading and bullet markers stripped.
_MIN_FIDELITY_BODY_CHARS = 40

#: Below this length a declaration name matches unrelated prose too easily
#: (``P``, ``add``), so it must appear as code rather than as a bare word.
_SHORT_NAME_LENGTH = 3

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class LeanIssue:
    """One blocking defect, in the ``literature_ledger`` shape.

    There is no non-blocking sibling on purpose. An earlier version had one,
    and everything routed into it became invisible: the completion hook only
    ever read the blocking list.
    """

    code: str
    path: str
    message: str

    def rendered(self) -> str:
        return f"{self.path}: {self.message}"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class LeanSourceEvidence:
    """What is known about one Lean source and the artifacts beside it."""

    source: Path
    fidelity: Path | None = None
    result: dict[str, Any] | None = None
    result_path: Path | None = None
    environment_failure: str = ""
    issues: tuple[LeanIssue, ...] = ()

    @property
    def verified(self) -> bool:
        """A current, complete, toolchain-backed ``success`` — the only pass."""
        return not self.issues and str(
            (self.result or {}).get("status") or ""
        ) == "success"


@dataclass(frozen=True)
class LeanEvidenceReport:
    """Everything the completion gate learned. All of it blocks or none of it."""

    sources: tuple[LeanSourceEvidence, ...] = ()
    issues: tuple[LeanIssue, ...] = ()

    @property
    def present(self) -> bool:
        return bool(self.sources)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sources": [str(item.source) for item in self.sources],
            "verified": [str(i.source) for i in self.sources if i.verified],
            "issues": [issue.as_dict() for issue in self.issues],
        }


# -- discovery ---------------------------------------------------------------

def _discover(project_root: Path) -> tuple[tuple[Path, ...], list[LeanIssue]]:
    """Project-authored ``.lean`` files plus anything that made the sweep partial.

    Stdlib only and deliberately so: a project with no Lean must not pay the
    cost of importing the checker, and must not fail closed on it either.

    Incompleteness is reported rather than absorbed. A sweep that could not
    read a directory, could not follow a link, or stopped at the ceiling has
    not established that the project is clean, and saying nothing would let a
    proof hide behind any of the three.
    """
    root = project_root.resolve()
    found: dict[Path, Path] = {}
    issues: list[LeanIssue] = []
    walk_errors: list[str] = []

    def on_error(exc: OSError) -> None:
        walk_errors.append(str(exc))

    truncated = False
    for current, dirnames, filenames in os.walk(
        root, onerror=on_error, followlinks=False
    ):
        keep: list[str] = []
        for name in sorted(dirnames):
            if name in _VENDOR_DIR_NAMES:
                continue
            directory = Path(current) / name
            if directory.is_symlink():
                # Not followed, so its contents were never examined; claiming
                # the project is clean would be claiming something unchecked.
                issues.append(
                    LeanIssue(
                        "lean_discovery_incomplete",
                        _display(directory, root),
                        "Lean discovery does not follow symlinked directories, "
                        "so any formalization inside this one is unchecked; "
                        "move it into the project tree",
                    )
                )
                continue
            keep.append(name)
        dirnames[:] = keep

        for name in sorted(filenames):
            if not name.endswith(".lean"):
                continue
            candidate = Path(current) / name
            if candidate.is_symlink():
                try:
                    target = candidate.resolve(strict=True)
                except OSError:
                    issues.append(
                        LeanIssue(
                            "lean_source_unreadable",
                            _display(candidate, root),
                            "Lean source is a broken symlink",
                        )
                    )
                    continue
                if not _within(target, root):
                    issues.append(
                        LeanIssue(
                            "lean_source_external",
                            _display(candidate, root),
                            "Lean source links outside the project, so the "
                            "evidence cannot be audited with it; keep the "
                            "formal source in the project",
                        )
                    )
                    continue
                candidate = target
            elif not candidate.is_file():
                continue
            found.setdefault(candidate.resolve(), candidate)
            if len(found) > MAX_DISCOVERED_SOURCES:
                truncated = True
                break
        if truncated:
            break

    if truncated:
        issues.append(
            LeanIssue(
                "lean_discovery_truncated",
                ".",
                f"more than {MAX_DISCOVERED_SOURCES} Lean sources were found, "
                "so the sweep stopped and the remainder is unchecked; keep "
                "vendored checkouts out of the project tree",
            )
        )
    for message in walk_errors:
        issues.append(
            LeanIssue(
                "lean_discovery_incomplete",
                ".",
                f"Lean discovery could not read part of the project ({message}), "
                "so the absence of further formalization is not established",
            )
        )
    return tuple(sorted(found.values())), issues


def discover_lean_sources(project_root: Path | str) -> tuple[Path, ...]:
    """The project's own ``.lean`` files, vendored and build trees pruned."""
    return _discover(Path(str(project_root)))[0]


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _fidelity_candidates(source: Path, project_root: Path) -> tuple[Path, ...]:
    """Directories that may hold the fidelity document, nearest first.

    Same directory first, because that is what ``prepare_canonical_lean_artifacts``
    materializes; then upward to the project root, so one document can cover a
    directory of related sources without being copied per file.
    """
    root = project_root.resolve()
    directory = source.parent.resolve()
    candidates: list[Path] = []
    for parent in (directory, *directory.parents):
        candidates.append(parent / _STATEMENT_FIDELITY_NAME)
        if parent == root:
            break
    return tuple(candidates)


def _find_fidelity(source: Path, project_root: Path) -> Path | None:
    for candidate in _fidelity_candidates(source, project_root):
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def _declaration_names(source_text: str) -> tuple[str, ...]:
    names = [
        match.group(1).strip("«»")
        for match in _DECLARATION.finditer(source_text)
    ]
    return tuple(dict.fromkeys(name for name in names if name))


def _mentions_declaration(text: str, name: str) -> bool:
    """Whether a fidelity document actually points at this declaration.

    Boundary-anchored rather than a bare substring: ``add`` must not be
    satisfied by the word "addition", and a short name like ``P`` must appear
    as code, because a bare capital letter occurs in ordinary prose.
    """
    if len(name) < _SHORT_NAME_LENGTH:
        return f"`{name}`" in text
    pattern = (
        rf"(?<![{_IDENTIFIER_CHARS}]){re.escape(name)}(?![{_IDENTIFIER_CHARS}])"
    )
    return re.search(pattern, text) is not None


def _fidelity_body_length(text: str) -> int:
    body = [
        line.strip().lstrip("#-*> \t")
        for line in text.splitlines()
        if line.strip()
    ]
    return sum(len(line) for line in body if line)


# -- environment vs. mathematics ---------------------------------------------

def classify_environment_failure(result: dict[str, Any] | None) -> str:
    """Say whether a failure is the environment's rather than the proof's.

    Returns ``"toolchain_absent"``, ``"audit_unavailable"``,
    ``"missing_dependency"``, or ``""`` when the compiler was equipped to judge
    the mathematics and judged it.

    This selects wording, not verdict. ``run_lean_check`` reports a missing
    Mathlib as ``type_error``, indistinguishable by status alone from a broken
    proof, and a reviewer reading the issue deserves to know which one they are
    looking at. Both still block: an unverified proof is unverified however
    good the excuse.
    """
    if not isinstance(result, dict):
        return ""
    status = str(result.get("status") or "")
    tools = result.get("tools")
    info = (
        tools.get(str(result.get("tool") or "")) if isinstance(tools, dict) else None
    )
    tool_available = isinstance(info, dict) and bool(info.get("available"))
    if status == _TOOLCHAIN_ABSENT_STATUS:
        # `unavailable` covers two different facts: no compiler at all, or a
        # compiler that ran but whose axiom audit could not. Saying "no Lean
        # toolchain" for the second would send the reader looking for elan.
        return "audit_unavailable" if tool_available else "toolchain_absent"
    if isinstance(info, dict) and info.get("available") is False:
        return "toolchain_absent"
    if status in {"success", "proof_hole"}:
        # A proof hole is a fact about the source, established before the
        # compiler ran; the environment cannot explain it either way.
        return ""
    blob = "\n".join(
        str(result.get(key) or "")
        for key in ("stdout", "stderr", "audit_stdout", "audit_stderr")
    ).lower()
    if any(pattern in blob for pattern in _MISSING_DEPENDENCY_PATTERNS):
        return "missing_dependency"
    return ""


def _missing_module_names(result: dict[str, Any] | None) -> tuple[str, ...]:
    blob = "\n".join(
        str((result or {}).get(key) or "")
        for key in ("stdout", "stderr", "audit_stdout", "audit_stderr")
    )
    names = re.findall(r"unknown module prefix '([^']+)'", blob)
    return tuple(dict.fromkeys(names))


# -- the recorded result -----------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_file(path: Path) -> str | None:
    try:
        return _sha256(path.read_bytes())
    except OSError:
        return None


def _digest_fidelity(path: Path) -> str | None:
    """The fidelity document's digest, taken the way every reader takes it.

    Through ``read_text`` rather than over raw bytes, because that is how
    ``verify_lean_source`` records it and how ``_fidelity_issues`` compares
    against it. Hashing the same document two ways would make an untouched note
    read as an edited one.
    """
    try:
        return _sha256(path.read_text(encoding="utf-8").encode("utf-8"))
    except (OSError, UnicodeError):
        return None


def _schema_problems(result: dict[str, Any], source: Path) -> list[str]:
    """Every way a recorded result can fail to be evidence of anything.

    A permissive reader is a forgery kit: ``{"status": "success"}`` is four
    keystrokes, and without this it certified a proof. Required fields, field
    types, and agreement between fields are all checked, because a record that
    contradicts itself was not produced by the compiler it claims to quote.
    """
    problems: list[str] = []

    status = result.get("status")
    if not isinstance(status, str) or status not in _KNOWN_STATUSES:
        problems.append(
            f"status {status!r} is not one of {', '.join(sorted(_KNOWN_STATUSES))}"
        )
        status = ""

    recorded_source = result.get("source")
    if not isinstance(recorded_source, str) or not recorded_source.strip():
        problems.append("source is missing; the result names no file")
    elif Path(recorded_source).name != source.name:
        problems.append(
            f"source names {Path(recorded_source).name!r}, not {source.name!r}"
        )

    digest = result.get("source_sha256")
    if not isinstance(digest, str) or not _SHA256_HEX.match(digest.strip().lower()):
        problems.append(
            "source_sha256 is missing or malformed; without it the result "
            "cannot be tied to the proof it claims to certify — re-run "
            "`lean_evidence verify`"
        )

    for key, kinds in (
        ("schema_version", int),
        ("tool", str),
        ("tools", dict),
        ("stdout", str),
        ("stderr", str),
        ("proof_holes", list),
    ):
        if not isinstance(result.get(key), kinds):
            problems.append(f"{key} is missing or has the wrong type")

    for key in ("exit_code", "audit_exit_code"):
        value = result.get(key)
        if key not in result or not (value is None or isinstance(value, int)):
            problems.append(f"{key} is missing or has the wrong type")

    holes = result.get("proof_holes")
    holes = holes if isinstance(holes, list) else []

    if status == "success":
        # The three things a genuine success always carries. `audit_exit_code`
        # of None means the axiom audit never ran, which is not a pass.
        if result.get("exit_code") != 0:
            problems.append(
                f"status is success but exit_code is {result.get('exit_code')!r}"
            )
        if result.get("audit_exit_code") != 0:
            problems.append(
                "status is success but the environment axiom audit did not "
                f"report success (audit_exit_code={result.get('audit_exit_code')!r}); "
                "a proof resting on an unaudited axiom is not a proof"
            )
        if holes:
            problems.append("status is success but proof holes are recorded")
    elif status == "proof_hole" and not holes:
        problems.append("status is proof_hole but no proof hole is recorded")
    return problems


def _load_result(source: Path) -> tuple[dict[str, Any] | None, Path | None, str]:
    path = source.parent / _LEAN_CHECK_RESULT_NAME
    if path.is_symlink():
        return None, path, "recorded Lean result is a symlink"
    if not path.is_file():
        return None, None, ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, path, "recorded Lean result is unreadable or not JSON"
    if not isinstance(payload, dict):
        return None, path, "recorded Lean result is not a JSON object"
    return payload, path, ""


# -- validation --------------------------------------------------------------

_VERIFY_HINT = (
    "run `python -m argus_skill.verticals.math.lean_evidence verify "
    "<source> --statement-fidelity <doc>`"
)

def _fallback_workspace() -> Path:
    """Where the search lands when nothing nearer applies.

    Imported from ``lean_check`` rather than restated: a remedy naming a path
    the search does not actually use is worse than no remedy. A function, not a
    constant, because ``Path.home()`` read at import time freezes an answer the
    search itself re-reads on every call.
    """
    from ...tools.lean_check import (  # noqa: PLC0415 — optional, heavy import
        default_mathlib_workspace,
    )

    return default_mathlib_workspace()


def resolved_mathlib_workspace(source: Path | str | None = None) -> Path | None:
    """The Lake workspace this host would compile ``source`` in, if any."""
    from ...tools.lean_check import (  # noqa: PLC0415 — optional, heavy import
        _resolve_lake_workspace,
    )

    probe = Path(str(source)) if source is not None else Path.cwd() / "Main.lean"
    return _resolve_lake_workspace(probe)


def _library_remedy(source: Path) -> str:
    """Say where the library was looked for, not merely that it is absent.

    "Provide the library" was the whole of this sentence for as long as the
    message existed, and it is advice only to someone who already knows the
    three places the search looks. The reader who most needs it is the one who
    installed Mathlib somewhere else and cannot see why it is invisible — for
    them the old wording pointed at the wrong problem entirely.

    The path is resolved at read time rather than quoted from the recorded run,
    because a result produced on a build box is often read somewhere else, and
    the question being answered is "what would fix this here".
    """
    workspace = resolved_mathlib_workspace(source)
    if workspace is not None:
        # The library is installed and reachable, so this run was told not to
        # use it. Naming the flag is the whole remedy.
        return (
            f"a Lake workspace does exist at {workspace}, so this run was told "
            "not to use it — drop `--no-lake` and re-run, or remove the source"
        )
    return (
        f"no Lake workspace applies to this source: install Mathlib at "
        f"{_fallback_workspace()}, or point "
        "ARGUS_SKILL_MATHLIB_WORKSPACE at an existing one, or put a "
        "lakefile.toml above the source. Then re-run verify, or remove the "
        "source"
    )


def _validate_source(source: Path, project_root: Path) -> LeanSourceEvidence:
    from ...tools.lean_check import find_proof_holes

    display = _display(source, project_root)
    issues: list[LeanIssue] = []
    try:
        source_bytes = source.read_bytes()
        source_text = source_bytes.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        return LeanSourceEvidence(
            source=source,
            issues=(
                LeanIssue(
                    "lean_source_unreadable",
                    display,
                    f"Lean source cannot be read as UTF-8: {exc}",
                ),
            ),
        )

    # 1. Proof holes. Pure lexing, no toolchain, and never excusable by one:
    #    a source with `sorry` in it asserts a theorem it does not prove.
    holes = find_proof_holes(source_text)
    if holes:
        rendered = ", ".join(
            f"{hole.get('kind')}@{hole.get('line')}" for hole in holes
        )
        issues.append(
            LeanIssue(
                "lean_proof_hole",
                display,
                f"Lean source contains proof holes or local assumptions ({rendered}); "
                "it does not prove what it states",
            )
        )

    # 2. The recorded compiler answer, loaded before fidelity is judged because
    #    the digest it carries is what makes the fidelity check a check about
    #    the document that was actually in force.
    result, result_path, load_note = _load_result(source)

    # 3. Statement fidelity. The one thing Lean structurally cannot check, and
    #    the reason a compiling proof can still be the wrong answer.
    fidelity = _find_fidelity(source, project_root)
    issues.extend(
        _fidelity_issues(source_text, fidelity, display, project_root, result)
    )

    # 4. Whether that answer is usable.
    environment_failure = ""
    if load_note:
        issues.append(LeanIssue("lean_result_unreadable", display, load_note))
    elif result is None:
        issues.append(
            LeanIssue(
                "lean_result_missing",
                display,
                "a Lean source is present with no recorded compiler result, so "
                f"it proves nothing yet; {_VERIFY_HINT} and commit what it says, "
                "or remove the source until it can be checked",
            )
        )
    else:
        environment_failure = classify_environment_failure(result)
        issues.extend(
            _result_issues(
                result,
                source,
                source_bytes,
                display,
                environment_failure,
            )
        )

    return LeanSourceEvidence(
        source=source,
        fidelity=fidelity,
        result=result,
        result_path=result_path,
        environment_failure=environment_failure,
        issues=tuple(issues),
    )


def _fidelity_issues(
    source_text: str,
    fidelity: Path | None,
    display: str,
    project_root: Path,
    result: dict[str, Any] | None = None,
) -> list[LeanIssue]:
    if fidelity is None:
        return [
            LeanIssue(
                "lean_fidelity_missing",
                display,
                "a Lean source needs a separate "
                f"{_STATEMENT_FIDELITY_NAME} stating how the formal statement "
                "corresponds to the mathematics claimed; Lean proves the "
                "statement you wrote, not the one you meant",
            )
        ]
    fidelity_display = _display(fidelity, project_root)
    try:
        text = fidelity.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [
            LeanIssue(
                "lean_fidelity_unreadable",
                fidelity_display,
                f"statement fidelity document cannot be read: {exc}",
            )
        ]
    if _fidelity_body_length(text) < _MIN_FIDELITY_BODY_CHARS:
        return [
            LeanIssue(
                "lean_fidelity_empty",
                fidelity_display,
                "statement fidelity document has no substantive body; it must "
                "say which objects, quantifiers, hypotheses, and conclusion the "
                "formal statement carries",
            )
        ]
    declarations = _declaration_names(source_text)
    if declarations and not any(
        _mentions_declaration(text, name) for name in declarations
    ):
        return [
            LeanIssue(
                "lean_fidelity_unlinked",
                fidelity_display,
                "statement fidelity document names none of the declarations it "
                f"must describe ({', '.join(declarations[:5])}); it cannot be "
                "checked against this source",
            )
        ]
    recorded = str((result or {}).get("statement_fidelity_sha256") or "").strip().lower()
    if recorded and recorded != _sha256(text.encode("utf-8")):
        return [
            LeanIssue(
                "lean_fidelity_changed",
                fidelity_display,
                "the statement fidelity document has been edited since this "
                "result was recorded, so the compiler's answer is paired with a "
                "reading of the theorem that was written afterwards. The "
                f"compile is still valid and its meaning is not; {_VERIFY_HINT}",
            )
        ]
    return []


def _result_issues(
    result: dict[str, Any],
    source: Path,
    source_bytes: bytes,
    display: str,
    environment_failure: str,
) -> list[LeanIssue]:
    problems = _schema_problems(result, source)
    if problems:
        return [
            LeanIssue(
                "lean_result_invalid",
                display,
                "recorded Lean result is not usable as evidence: "
                + "; ".join(problems),
            )
        ]

    digest = str(result.get("source_sha256") or "").strip().lower()
    if digest != _sha256(source_bytes):
        return [
            LeanIssue(
                "lean_result_stale",
                display,
                "the source has changed since this result was recorded, so the "
                f"recorded outcome describes a different proof; {_VERIFY_HINT}",
            )
        ]

    status = str(result.get("status"))
    if status == "success":
        return []

    if environment_failure == "toolchain_absent":
        return [
            LeanIssue(
                "lean_unverified_toolchain_absent",
                display,
                "this Lean source has never been verified: no Lean toolchain "
                "was available when it ran. This is an environment gap, not a "
                "mathematical defect — but an unverified formalization is not "
                "evidence. Install the toolchain, or remove the source",
            )
        ]
    if environment_failure == "missing_dependency":
        missing = ", ".join(_missing_module_names(result)) or "a Lean library"
        return [
            LeanIssue(
                "lean_unverified_missing_dependency",
                display,
                f"this Lean source has never been verified: {missing} is not in "
                "the search path, so compilation stopped before reaching the "
                "mathematics. This is an environment gap, not a mathematical "
                "defect — but an unverified formalization is not evidence. "
                + _library_remedy(source),
            )
        ]
    if environment_failure == "audit_unavailable":
        return [
            LeanIssue(
                "lean_unverified_audit_failed",
                display,
                "the file compiled but the environment axiom audit could not "
                "run, so it is unknown whether the proof rests on an axiom. "
                "This is an environment gap, not a mathematical defect — but an "
                "unaudited proof is not evidence; re-run the check on a working "
                "toolchain",
            )
        ]
    if status == "proof_hole":
        declared = ", ".join(
            str(hole.get("declaration") or hole.get("kind"))
            for hole in (result.get("proof_holes") or [])
        )
        return [
            LeanIssue(
                "lean_proof_hole",
                display,
                "the recorded compiler run found a proof hole or axiom "
                f"dependency ({declared or 'unspecified'})",
            )
        ]
    if status == "timeout":
        return [
            LeanIssue(
                "lean_compile_timeout",
                display,
                "the recorded compiler run timed out, so the proof is "
                "unverified; raise the timeout or reduce the file, but do not "
                "report it as checked",
            )
        ]
    detail = _first_error_line(result)
    return [
        LeanIssue(
            "lean_compile_failed",
            display,
            f"the recorded compiler run failed ({status})"
            + (f": {detail}" if detail else ""),
        )
    ]


def _first_error_line(result: dict[str, Any]) -> str:
    blob = "\n".join(str(result.get(key) or "") for key in ("stdout", "stderr"))
    for line in blob.splitlines():
        if "error" in line.lower():
            return line.strip()[:200]
    return ""


def _display(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except (ValueError, OSError):
        return str(path)


def validate_lean_evidence(project_root: Path | str) -> LeanEvidenceReport:
    """Judge every Lean source the project authored.

    Returns an empty report — no issues, and no imports beyond the standard
    library — when the project has no Lean at all, which is the ordinary case
    for mathematics that is not being formalized.
    """
    root = Path(str(project_root))
    sources, issues = _discover(root)
    if not sources and not issues:
        return LeanEvidenceReport()

    evidence: list[LeanSourceEvidence] = []
    for source in sources:
        item = _validate_source(source, root)
        evidence.append(item)
        issues.extend(item.issues)
    return LeanEvidenceReport(sources=tuple(evidence), issues=tuple(issues))


# -- the completion hook -----------------------------------------------------

#: Memo keyed by the *content* of every artifact the verdict depends on, not by
#: its metadata. Keying on size and mtime was wrong in a way that mattered: a
#: broken proof of the same byte length, with `os.utime` used to restore the
#: timestamp, reused the previous pass. A digest cannot be restored that way.
_CACHE: dict[tuple[Any, ...], tuple[str, ...]] = {}
_CACHE_LIMIT = 64


def _cache_key(project_root: Path, sources: tuple[Path, ...]) -> tuple[Any, ...]:
    parts: list[Any] = [str(project_root.resolve())]
    for source in sources:
        parts.append(("source", str(source), _digest_file(source)))
        result = source.parent / _LEAN_CHECK_RESULT_NAME
        parts.append(("result", str(result), _digest_file(result)))
        # Every location `_find_fidelity` would consult, not just the nearest
        # one: editing a fidelity document at the project root has to
        # invalidate a verdict that was computed from it.
        for candidate in _fidelity_candidates(source, project_root):
            parts.append(("fidelity", str(candidate), _digest_file(candidate)))
    return tuple(parts)


def lean_evidence_issues(project_root: Path | str) -> tuple[str, ...]:
    """Blocking Lean issues for the completion gate, or ``()`` when there is no Lean.

    Never spawns a compiler: it reads what a compiler already said. That keeps
    a completion decision at filesystem cost even when the project carries a
    Mathlib-scale proof that takes minutes to build.
    """
    root = Path(str(project_root))
    sources, discovery_issues = _discover(root)
    if not sources and not discovery_issues:
        return ()

    key = _cache_key(root, sources)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    rendered = tuple(
        issue.rendered() for issue in validate_lean_evidence(root).issues
    )
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[key] = rendered
    return rendered


def source_evidence(source: Path | str, project_root: Path | str) -> LeanSourceEvidence:
    """Judge one Lean source and the artifacts beside it, without discovery.

    ``validate_lean_evidence`` sweeps a project and is what the completion gate
    wants. A caller holding one specific source — the kernel recorder in
    ``math_state`` — wants exactly the same judgement about exactly that file,
    and must not get a pass because some *other* source in the tree was the one
    with a defect, nor a refusal because some unrelated source was broken.

    Sources outside the project are refused rather than judged: an artifact path
    recorded in project state has to name something inside the project, or the
    record cites a file that no reader of the repository can see.
    """
    root = Path(str(project_root)).expanduser().resolve()
    path = Path(str(source)).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return LeanSourceEvidence(
            source=path,
            issues=(
                LeanIssue(
                    "lean_source_external",
                    str(path),
                    f"the Lean source is outside the project root {root}; "
                    "evidence must cite an artifact the project carries",
                ),
            ),
        )
    return _validate_source(path, root)


# -- producing the evidence --------------------------------------------------

class CompiledArtifactChangedError(ValueError):
    """An artifact moved under the compiler, so no certificate may be written.

    Deliberately not a status. ``unverified`` means "the environment could not
    answer", and this is a different fact: the environment answered, about text
    that no longer exists. There is nothing to record and nothing to downgrade —
    a certificate that cannot be trusted must not be published at all.

    A ``ValueError`` because that is what this module's other refusals already
    are, so the ``verify`` CLI arm and every caller with an ``except ValueError``
    treats it as a refusal rather than a crash; a distinct type because "the run
    was raced" and "the arguments were malformed" call for different repairs.
    """


def _drift(label: str, path: Path, before: str, after: str | None) -> str | None:
    if after == before:
        return None
    if after is None:
        return f"{label} ({path}) could no longer be read after the compile"
    return f"{label} ({path}) changed while the compiler was running"


def _refuse_if_moved_under_the_compiler(
    source: Path,
    source_digest: str,
    fidelity: Path,
    fidelity_digest: str,
) -> None:
    """Refuse to certify a compile whose inputs did not hold still.

    The digests were taken before ``run_lean_check`` was called; these are the
    same files afterwards. If either moved, the compiler's answer and the bytes
    on disk are about different text, and there is no honest record to write:
    stamping the run with the digests taken beforehand publishes a verdict about
    a file the project no longer has, and stamping it with the digests taken
    afterwards — which is what this module did — hands the *new* text the *old*
    text's verdict, with ``lean_evidence check`` then finding everything current
    and consistent. Both are worse than no certificate.

    The window is real rather than theoretical. ``prepare_canonical_lean_artifacts``
    snapshots the source only when it is not already the canonical
    ``Main.lean``, and the documented Engineer invocation compiles
    ``research/lean/Main.lean`` in place, so what the compiler reads is the live
    file in the working tree for as long as the compile takes.
    """
    reasons = [
        reason
        for reason in (
            _drift("the Lean source", source, source_digest, _digest_file(source)),
            _drift(
                f"the {_STATEMENT_FIDELITY_NAME}",
                fidelity,
                fidelity_digest,
                _digest_fidelity(fidelity),
            ),
        )
        if reason
    ]
    if not reasons:
        return
    raise CompiledArtifactChangedError(
        "; ".join(reasons)
        + ". The compiler's answer is therefore about text this project no "
        "longer carries, so nothing was recorded — no result, no compile log, "
        "no claim evidence. Leave the file alone and run verify again."
    )


def verify_lean_source(
    source: Path | str,
    *,
    statement_fidelity: Path | str,
    artifact_dir: Path | str | None = None,
    timeout_seconds: float | None = None,
    lean_bin: str | None = None,
    lake_bin: str | None = None,
    use_lake: bool | None = None,
) -> dict[str, Any]:
    """Compile one source and record the answer beside it, hash included.

    Thin over ``lean_check``: it reuses that module's canonical artifact
    preparation, directory lock, atomic writes, and log rendering, and adds two
    digests. ``source_sha256`` is what later lets staleness be decided by
    identity rather than guessed from modification order.
    ``statement_fidelity_sha256`` does the same for the other half of the
    argument: the compiler's answer is only evidence about a mathematical claim
    in the presence of a document saying what the formal statement means, and
    without a digest that document could be rewritten after the compile to make
    a proof of one thing read as a proof of another — with every existing check
    still passing, because they only ask whether *some* substantive note names
    the declaration. Nothing here establishes that the note is *true*; no tool
    in this repository does, and no field claims otherwise.

    Both digests are taken *before* the compiler is started and re-taken after
    it returns, and a run whose source or note moved in between publishes
    nothing at all — see ``_refuse_if_moved_under_the_compiler``. Hashing after
    the compile, which is what this did, meant an edit landing inside the
    compile window produced a certificate carrying the new text's digest and the
    old text's verdict; every later check then agreed the record was current,
    because the recorded digest and the file on disk matched perfectly. There is
    no ordering of one read that fixes this, only two reads and a refusal.

    ``use_lake`` defaults to ``None``, meaning *decide from the host*: compile
    through ``lake env lean`` when a Lake workspace applies to this source, and
    through bare ``lean`` when none does. It used to default to ``False``, and
    that turned an installed Mathlib into a worse failure than no Mathlib at
    all — the library was on disk, the import still resolved to nothing, and
    the recorded verdict said ``missing_dependency``, which reads as "install
    Mathlib" to the one reader who already had. An Engineer cannot be expected
    to pass a flag whose only effect is to undo a default that was wrong.

    The decision stays here rather than in ``run_lean_check`` on purpose. That
    function is the primitive: it does what it is told, so its behaviour is a
    function of its arguments alone and its tests mean the same thing on every
    host. Which toolchain a *mission* should use is a policy question, and this
    is where the Math vertical answers it. The answer is recorded as
    ``lake_workspace`` in the result, because a compile whose search path was
    chosen for the caller must say so; an unexplained ``cwd`` three directories
    away is not an explanation.
    """
    from ...tools.lean_check import (  # noqa: PLC0415 — optional, heavy import
        COMPILE_LOG,
        LEAN_CHECK_RESULT,
        _artifact_directory_lock,
        _atomic_artifact_write,
        _resolve_lake_workspace,
        prepare_canonical_lean_artifacts,
        render_compile_log,
        run_lean_check,
    )

    source_path = Path(str(source)).expanduser().resolve()
    root = (
        Path(str(artifact_dir)).expanduser().resolve()
        if artifact_dir is not None
        else source_path.parent
    )
    with _artifact_directory_lock(root):
        canonical_source, canonical_fidelity = prepare_canonical_lean_artifacts(
            source_path,
            root,
            statement_fidelity,
        )
        workspace = _resolve_lake_workspace(canonical_source)
        through_lake = workspace is not None if use_lake is None else use_lake
        # Read once, here, and record *these* digests. What the certificate has
        # to bind is the verdict to the bytes the verdict is about, and bytes
        # read after `run_lean_check` returns are not necessarily those bytes.
        compiled_bytes = canonical_source.read_bytes()
        compiled_fidelity = canonical_fidelity.read_text(encoding="utf-8")
        source_digest = _sha256(compiled_bytes)
        # Hashed through `read_text` rather than from the raw bytes because the
        # check that later compares against it reads the document the same way;
        # a digest over bytes would report a BOM as a changed meaning.
        fidelity_digest = _sha256(compiled_fidelity.encode("utf-8"))
        result = run_lean_check(
            canonical_source,
            timeout_seconds=timeout_seconds,
            lean_bin=lean_bin,
            lake_bin=lake_bin,
            use_lake=through_lake,
        )
        # Before anything is published, and before the caller can act on the
        # returned dict: a raced run has no certificate, not a weakened one.
        _refuse_if_moved_under_the_compiler(
            canonical_source,
            source_digest,
            canonical_fidelity,
            fidelity_digest,
        )
        result["source_sha256"] = source_digest
        result["statement_fidelity"] = str(canonical_fidelity)
        result["statement_fidelity_sha256"] = fidelity_digest
        result["lake_workspace"] = str(workspace) if through_lake and workspace else ""
        result["environment_failure"] = classify_environment_failure(result)
        _atomic_artifact_write(
            root / LEAN_CHECK_RESULT,
            (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        _atomic_artifact_write(
            root / COMPILE_LOG,
            render_compile_log(result).encode("utf-8"),
        )
    return result


# -- CLI ---------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="judge recorded Lean evidence")
    check.add_argument("--project-root", type=Path, default=Path("."))

    verify = sub.add_parser("verify", help="compile one source and record it")
    _add_compile_arguments(verify)

    # `submit` takes exactly what `verify` takes, because it is the same step
    # with the waiting removed. A verb that quietly accepted a different set
    # would make the two answer differently, which is the one thing an
    # asynchronous path must never do.
    submit = sub.add_parser(
        "submit",
        help="start the same compile in the background and print a handle",
    )
    _add_compile_arguments(submit)

    reclaim = sub.add_parser(
        "reclaim",
        help="record the answer from a submitted compile, once it has one",
    )
    reclaim.add_argument(
        "handle",
        help=(
            "the handle `submit` printed. Everything else about the run — the "
            "source, the fidelity document, the claim — was settled at submit "
            "and is not re-stated here"
        ),
    )

    sub.add_parser("status", help="background compiles this host still holds")

    sub.add_parser("audit", help="report the Lean toolchain this host has")
    return parser


def _add_compile_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source", type=Path)
    parser.add_argument("--statement-fidelity", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument(
        "--claim",
        help=(
            "record the outcome as mechanical evidence about this claim in "
            "research/MATH_STATE.json. This is the only way mechanical "
            "evidence is ever written: the tier is chosen by the code that "
            "read the compiler's answer, never passed in"
        ),
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
        help="the project whose state --claim writes to, and the root artifact paths are recorded against",
    )
    parser.add_argument(
        "--timeout", type=float, default=None,
        help="explicit compile timeout; omitted waits for Lean to finish",
    )
    parser.add_argument("--lean-bin")
    parser.add_argument("--lake-bin")
    parser.add_argument(
        "--lake",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "compile through `lake env lean`. Omit to decide from the host: "
            "lake when a Lake workspace applies to the source, bare lean "
            "otherwise. Pass --no-lake to force bare lean even where a "
            "workspace exists"
        ),
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "audit":
        from ...tools.lean_check import audit_lean_tools

        # `audit_lean_tools` reports executables; the question an Engineer
        # actually arrives with is "will `import Mathlib` resolve", and three
        # working binaries do not answer it. Reported alongside rather than
        # folded in, so the shared primitive keeps its shape.
        workspace = resolved_mathlib_workspace()
        payload: dict[str, Any] = dict(audit_lean_tools())
        payload["mathlib_workspace"] = {
            "resolved": str(workspace) if workspace else "",
            "searched": [
                "a lakefile.toml/lakefile.lean above the source",
                "$ARGUS_SKILL_MATHLIB_WORKSPACE",
                str(_fallback_workspace()),
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "verify":
        try:
            result = verify_lean_source(
                args.source,
                statement_fidelity=args.statement_fidelity,
                artifact_dir=args.artifact_dir,
                timeout_seconds=args.timeout,
                lean_bin=args.lean_bin,
                lake_bin=args.lake_bin,
                use_lake=args.lake,
            )
        except (OSError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 2
        payload = dict(result)
        if args.claim:
            # The canonical source, not `args.source`: verify may have copied a
            # descriptively-named file into the artifact directory, and what was
            # compiled is what the evidence is about.
            from .math_state import (  # noqa: PLC0415 — avoids an import cycle
                record_lean_evidence,
            )

            recording = record_lean_evidence(
                args.project_root,
                claim_id=args.claim,
                source=Path(str(result.get("source") or args.source)),
                expect_result=result,
            )
            payload["kernel"] = recording.as_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if result.get("status") != "success":
            return 1
        # A compile that succeeded and was asked to be recorded, but was not,
        # has not done what it was told to do. Reporting 0 here would let a
        # completion gate read "proved" off an exit code while the state file
        # holds nothing.
        return 1 if args.claim and payload["kernel"]["recorded"] is None else 0

    if args.command == "submit":
        from .lean_async import submit_lean_run  # noqa: PLC0415 — optional path

        try:
            started = submit_lean_run(
                args.source,
                statement_fidelity=args.statement_fidelity,
                artifact_dir=args.artifact_dir,
                project_root=args.project_root,
                claim=args.claim or "",
                timeout_seconds=args.timeout,
                lean_bin=args.lean_bin,
                lake_bin=args.lake_bin,
                use_lake=args.lake,
            )
        except (OSError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps(started, ensure_ascii=False, indent=2))
        return 0

    if args.command == "reclaim":
        from .lean_async import reclaim_lean_run  # noqa: PLC0415 — optional path

        try:
            payload = reclaim_lean_run(args.handle)
        except (OSError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        # A run that has not finished is not a verdict of any kind, so it gets
        # an exit code of its own: 0 would read as a pass and 1 as a failing
        # proof, and the compiler has said neither.
        if payload.get("state") == "running":
            return 3
        if payload.get("status") != "success":
            return 1
        return 1 if "kernel" in payload and payload["kernel"]["recorded"] is None else 0

    if args.command == "status":
        from .lean_async import outstanding_runs  # noqa: PLC0415 — optional path

        print(
            json.dumps({"runs": outstanding_runs()}, ensure_ascii=False, indent=2)
        )
        return 0

    report = validate_lean_evidence(args.project_root.expanduser().resolve())
    payload = report.as_dict()
    payload["ok"] = not report.issues
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if report.issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
