"""Lean evidence entering the Math completion decision.

The vertical shipped a complete, fail-closed Lean checker that nothing called.
A project could therefore carry a `.lean` file containing `sorry`, or a proof
of a statement that did not say what the project claimed, and still complete —
the only mechanical gate was whether one JSON file existed.

Two properties are load-bearing and pull against each other:

* Almost no mathematics project formalizes anything, so a project with no Lean
  must be completely unaffected. That regression guard comes first below.
* A project that does formalize must not be able to present an unproved,
  unverified, stale, forged, mistranslated, or merely unfound formalization as
  evidence.

Most of what follows is adversarial: each test builds the specific trick that
would buy an undeserved pass and asserts it does not. The tricks are cheap —
a four-key JSON file, a same-length edit with the timestamp restored, a proof
moved into `build/` — which is exactly why they need tests rather than care.

An earlier version of this module let environment failures pass on the grounds
that a compile which never reached the mathematics says nothing about the
mathematics. The premise is right and is still reported; the conclusion was
wrong. Serious formalization imports Mathlib, this host has no Mathlib, and
every such compile fails — so excusing those failures switched the gate off in
precisely the case it exists for. Those tests are inverted below, marked where
they sit.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from argus_skill.tools import lean_check
from argus_skill.tools.lean_check import audit_lean_tools
from argus_skill.verticals.math.lean_evidence import (
    MAX_DISCOVERED_SOURCES,
    CompiledArtifactChangedError,
    classify_environment_failure,
    discover_lean_sources,
    lean_evidence_issues,
    main,
    validate_lean_evidence,
    verify_lean_source,
)
from argus_skill.verticals.math.objective_mode import set_objective
from argus_skill.verticals.math.stages import stage_completion_issues

CORE_THEOREM = (
    "theorem argus_add_comm (a b : Nat) : a + b = b + a := Nat.add_comm a b\n"
)
MATHLIB_THEOREM = (
    "import Mathlib\n\n"
    "theorem argus_dvd_add (a b c : Int) (h : a ∣ b) (k : a ∣ c) : "
    "a ∣ (b + c) := dvd_add h k\n"
)


def _symlink_or_skip(
    link: Path, target: Path, *, target_is_directory: bool = False
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise
FIDELITY = (
    "# Statement fidelity\n\n"
    "`argus_add_comm` formalizes: for all natural numbers a and b, a + b = b + a.\n"
    "Objects: natural numbers. Quantifiers: universal over a and b.\n"
    "Hypotheses: none. Conclusion: commutativity of addition. Added assumptions: none.\n"
)

_TOOLS = audit_lean_tools()
_HAS_LEAN = bool(_TOOLS.get("lean", {}).get("available"))
requires_lean = pytest.mark.skipif(
    not _HAS_LEAN, reason="no Lean toolchain on this host"
)


# -- fixtures ---------------------------------------------------------------

def _project(tmp_path: Path, *, profile: str = "develop") -> Path:
    set_objective(tmp_path, mode="targeted", goal="G")
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["verification_profile"] = profile
    state_path.write_text(json.dumps(state), encoding="utf-8")
    # A satisfied proof graph, so anything left is attributable to Lean.
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PROOF_GRAPH.json").write_text(
        json.dumps({
            "goal": "G",
            "routes": [{"name": "route", "status": "current", "evidence": ""}],
            "nodes": {
                "G": {
                    "statement": "G",
                    "status": "proved",
                    "is_goal": True,
                    "depends_on": [],
                    "reviewer_confirmed": True,
                }
            },
        }),
        encoding="utf-8",
    )
    return tmp_path


def _lean_dir(root: Path) -> Path:
    path = root / "research" / "lean"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_source(root: Path, text: str = CORE_THEOREM, name: str = "Main.lean") -> Path:
    path = _lean_dir(root) / name
    path.write_text(text, encoding="utf-8")
    return path


def _write_fidelity(root: Path, text: str = FIDELITY) -> Path:
    path = _lean_dir(root) / "statement_fidelity.md"
    path.write_text(text, encoding="utf-8")
    return path


def _write_result(root: Path, name: str = "Main.lean", **overrides) -> Path:
    """A complete, hash-stamped success — the only shape that passes."""
    source = _lean_dir(root) / name
    payload = {
        "schema_version": 1,
        "status": "success",
        "source": str(source),
        "tool": "lean",
        "tools": {"lean": {"available": True, "path": "/usr/bin/lean", "version": "4"}},
        "command": ["/usr/bin/lean", str(source)],
        "cwd": str(_lean_dir(root)),
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "proof_holes": [],
        "audit_command": [],
        "audit_exit_code": 0,
        "audit_stdout": "",
        "audit_stderr": "",
        "duration_ms": 10,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    payload.update(overrides)
    for key in [k for k, v in payload.items() if v is _ABSENT]:
        del payload[key]
    path = _lean_dir(root) / "lean_check.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _Absent:
    """Marker letting a test delete a field rather than only override it."""


_ABSENT = _Absent()


def _codes(root: Path) -> set[str]:
    return {issue.code for issue in validate_lean_evidence(root).issues}


def _message(root: Path, code: str) -> str:
    """The rendered text of one blocking issue — what a reader actually acts on."""
    matches = [
        issue.message
        for issue in validate_lean_evidence(root).issues
        if issue.code == code
    ]
    assert matches, f"{code} not raised; got {_codes(root)}"
    return matches[0]


def _sound(root: Path) -> Path:
    """A project carrying one Lean proof that genuinely passes every check."""
    source = _write_source(root)
    _write_fidelity(root)
    _write_result(root)
    assert validate_lean_evidence(root).issues == ()
    return source


# -- the regression that matters most ---------------------------------------

def test_a_project_without_lean_is_completely_unaffected(tmp_path: Path) -> None:
    root = _project(tmp_path)

    assert discover_lean_sources(root) == ()
    assert lean_evidence_issues(root) == ()
    assert validate_lean_evidence(root).issues == ()
    assert validate_lean_evidence(root).present is False
    assert stage_completion_issues("solve", root) == ()
    assert stage_completion_issues("review", root) == ()


def test_a_project_without_lean_never_loads_the_checker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Formalization is optional, so its cost must be optional too."""
    import argus_skill.verticals.math.lean_evidence as module

    def explode(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("the Lean checker was imported without any Lean")

    monkeypatch.setattr(module, "_validate_source", explode)

    assert lean_evidence_issues(_project(tmp_path)) == ()


def test_vendored_dependencies_are_not_the_projects_own_work(tmp_path: Path) -> None:
    root = _project(tmp_path)
    for relative in (
        ".lake/packages/mathlib/Mathlib/Order/Basic.lean",
        "research/lean/.lake/build/Vendored.lean",
        "lake-packages/std/Std/Data.lean",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("theorem vendored : True := by trivial\n", encoding="utf-8")

    assert discover_lean_sources(root) == ()
    assert stage_completion_issues("solve", root) == ()


# -- statement fidelity: the gate this wiring exists for --------------------

def test_a_lean_source_without_statement_fidelity_is_an_issue(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _write_source(root)
    _write_result(root)

    assert "lean_fidelity_missing" in _codes(root)
    joined = " ".join(stage_completion_issues("solve", root))
    assert "statement_fidelity.md" in joined
    # Lean proves the statement you wrote, not the one you meant.
    assert "not the one you meant" in joined


def test_a_placeholder_fidelity_document_does_not_satisfy_the_gate(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    _write_source(root)
    _write_result(root)
    _write_fidelity(root, "# Statement fidelity\n")

    # Judged by content, not by existence.
    assert "lean_fidelity_empty" in _codes(root)
    assert stage_completion_issues("solve", root) != ()


def test_a_fidelity_document_about_something_else_is_rejected(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _write_source(root)
    _write_result(root)
    _write_fidelity(
        root,
        "# Statement fidelity\n\nThis describes an entirely different result "
        "about the distribution of primes in arithmetic progressions.\n",
    )

    assert "lean_fidelity_unlinked" in _codes(root)


def test_a_declaration_name_must_match_on_identifier_boundaries(
    tmp_path: Path,
) -> None:
    """Review point 11: substring matching accepted unrelated prose.

    `add` occurs inside "addition", "added", and "address". A document that
    never mentions the declaration must not be linked to it by accident.
    """
    root = _project(tmp_path)
    _write_source(root, "theorem add (a b : Nat) : a + b = b + a := Nat.add_comm a b\n")
    _write_result(root)
    _write_fidelity(
        root,
        "# Statement fidelity\n\nThis document is about addition of integers, "
        "and no assumptions were added; the address of the claim is elsewhere.\n",
    )

    assert "lean_fidelity_unlinked" in _codes(root)

    # Naming it as code links it.
    _write_fidelity(
        root,
        "# Statement fidelity\n\n`add` formalizes commutativity over the natural "
        "numbers: universally quantified in a and b, no hypotheses.\n",
    )
    assert "lean_fidelity_unlinked" not in _codes(root)


def test_a_short_declaration_name_must_appear_as_code(tmp_path: Path) -> None:
    """A bare capital letter appears in ordinary prose too easily."""
    root = _project(tmp_path)
    _write_source(root, "theorem P : True := trivial\n")
    _write_result(root)
    _write_fidelity(
        root,
        "# Statement fidelity\n\nProposition P holds trivially; objects none, "
        "quantifiers none, hypotheses none, conclusion True.\n",
    )

    assert "lean_fidelity_unlinked" in _codes(root)


def test_a_real_fidelity_document_satisfies_the_gate(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _sound(root)

    assert stage_completion_issues("solve", root) == ()


def test_one_fidelity_document_can_cover_a_directory_of_sources(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    _write_source(root)
    _write_result(root)
    (root / "research" / "statement_fidelity.md").write_text(
        FIDELITY, encoding="utf-8"
    )

    assert "lean_fidelity_missing" not in _codes(root)


# -- proof validity ---------------------------------------------------------

@pytest.mark.parametrize("hole", ["sorry", "admit"])
def test_a_proof_hole_is_an_issue_without_any_toolchain(
    tmp_path: Path,
    hole: str,
) -> None:
    root = _project(tmp_path)
    _write_source(root, f"theorem argus_bad : True := by\n  {hole}\n")
    _write_fidelity(
        root,
        "# Statement fidelity\n\n`argus_bad` claims True, which is trivially "
        "the case; objects, quantifiers, hypotheses and conclusion all empty.\n",
    )
    _write_result(root)

    assert "lean_proof_hole" in _codes(root)
    joined = " ".join(stage_completion_issues("solve", root))
    assert hole in joined
    assert "does not prove what it states" in joined


def test_a_local_axiom_is_a_proof_hole(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _write_source(
        root,
        "axiom argus_forged : False\ntheorem argus_bogus : False := argus_forged\n",
    )
    _write_fidelity(
        root,
        "# Statement fidelity\n\n`argus_bogus` derives False, and the objects, "
        "hypotheses and conclusion are all empty by construction.\n",
    )
    _write_result(root)

    assert "lean_proof_hole" in _codes(root)


def test_a_recorded_compile_failure_is_an_issue(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _write_source(root)
    _write_fidelity(root)
    _write_result(
        root,
        status="type_error",
        exit_code=1,
        audit_exit_code=None,
        stdout="Main.lean:1:44: error: type mismatch\n  Nat.add_comm a b\n",
    )

    assert "lean_compile_failed" in _codes(root)
    assert "type mismatch" in " ".join(stage_completion_issues("solve", root))


def test_a_recorded_compiler_proof_hole_is_an_issue(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _write_source(root)
    _write_fidelity(root)
    _write_result(
        root,
        status="proof_hole",
        proof_holes=[{"kind": "environment_axiom", "declaration": "forged"}],
    )

    assert "lean_proof_hole" in _codes(root)


def test_a_success_claiming_a_failed_axiom_audit_is_not_evidence(
    tmp_path: Path,
) -> None:
    """`run_lean_check` never emits this pair, so a record carrying it is forged."""
    root = _project(tmp_path)
    _write_source(root)
    _write_fidelity(root)
    _write_result(root, status="success", audit_exit_code=3)

    assert "lean_result_invalid" in _codes(root)
    assert "axiom audit" in " ".join(stage_completion_issues("solve", root))


def test_a_recorded_timeout_is_not_treated_as_verified(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _write_source(root)
    _write_fidelity(root)
    _write_result(root, status="timeout", exit_code=None, audit_exit_code=None)

    assert "lean_compile_timeout" in _codes(root)


# -- the recorded result must be a real record ------------------------------

def test_a_minimal_hand_written_pass_is_not_evidence(tmp_path: Path) -> None:
    """Review point 3: `{"status": "success"}` used to certify a proof."""
    root = _project(tmp_path)
    _write_source(root)
    _write_fidelity(root)
    (_lean_dir(root) / "lean_check.json").write_text(
        json.dumps({"status": "success"}), encoding="utf-8"
    )

    assert "lean_result_invalid" in _codes(root)
    assert stage_completion_issues("solve", root) != ()


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "source",
        "tool",
        "tools",
        "exit_code",
        "stdout",
        "stderr",
        "proof_holes",
        "audit_exit_code",
        "source_sha256",
    ],
)
def test_every_required_field_is_actually_required(
    tmp_path: Path,
    field: str,
) -> None:
    root = _project(tmp_path)
    _write_source(root)
    _write_fidelity(root)
    _write_result(root, **{field: _ABSENT})

    assert "lean_result_invalid" in _codes(root), field


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "1"),
        ("tools", []),
        ("proof_holes", "none"),
        ("exit_code", "0"),
        ("stdout", None),
        ("status", "verified"),
        ("source_sha256", "abc"),
    ],
)
def test_a_mistyped_field_is_not_evidence(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    root = _project(tmp_path)
    _write_source(root)
    _write_fidelity(root)
    _write_result(root, **{field: value})

    assert "lean_result_invalid" in _codes(root), field


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "success", "exit_code": 1},
        {"status": "success", "proof_holes": [{"kind": "sorry", "line": 2}]},
        {"status": "proof_hole", "proof_holes": []},
    ],
)
def test_a_self_contradicting_result_is_not_evidence(
    tmp_path: Path,
    overrides: dict,
) -> None:
    """A record that disagrees with itself was not produced by a compiler."""
    root = _project(tmp_path)
    _write_source(root)
    _write_fidelity(root)
    _write_result(root, **overrides)

    assert "lean_result_invalid" in _codes(root), overrides


def test_a_result_without_a_hash_cannot_certify_anything(tmp_path: Path) -> None:
    """Review point 7: a hash-less record is unverifiable, not merely old.

    The generic `lean_check` CLI writes no hash, and modification order is not
    a substitute — `os.utime` rewrites it in one call.
    """
    root = _project(tmp_path)
    source = _write_source(root)
    _write_fidelity(root)
    result = _write_result(root)
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload.pop("source_sha256")
    result.write_text(json.dumps(payload), encoding="utf-8")
    # Even with the source made to look older than the result.
    stamp = result.stat().st_mtime_ns - 10**9
    os.utime(source, ns=(stamp, stamp))

    assert "lean_result_invalid" in _codes(root)
    assert "lean_evidence verify" in " ".join(stage_completion_issues("solve", root))


def test_a_result_naming_another_source_is_not_borrowed(tmp_path: Path) -> None:
    """Review point 10, half one: identity must match."""
    root = _project(tmp_path)
    _write_source(root)
    _write_fidelity(root)
    _write_result(root, source=str(_lean_dir(root) / "Other.lean"))

    assert "lean_result_invalid" in _codes(root)


def test_a_result_for_a_different_proof_is_not_reused(tmp_path: Path) -> None:
    """Review point 10, half two: a matching name is not a matching proof.

    A pass recorded for `Main.lean` must not certify a `Main.lean` that was
    replaced afterwards, even though the filename in the record still agrees.
    """
    root = _project(tmp_path)
    source = _write_source(root)
    _write_fidelity(root)
    _write_result(root)

    source.write_text(
        "theorem argus_add_comm (a b : Nat) : a + b = b + a := by\n  sorry\n",
        encoding="utf-8",
    )

    codes = _codes(root)
    assert "lean_result_stale" in codes
    assert "lean_proof_hole" in codes


def test_two_sources_cannot_share_one_pass(tmp_path: Path) -> None:
    """Both live in the same directory, so both see the same `lean_check.json`."""
    root = _project(tmp_path)
    _write_source(root)
    _write_source(root, "theorem argus_other : True := trivial\n", name="Other.lean")
    _write_fidelity(
        root,
        FIDELITY + "\n`argus_other` states True, trivially, with nothing assumed.\n",
    )
    _write_result(root)

    issues = {
        (issue.path, issue.code) for issue in validate_lean_evidence(root).issues
    }
    assert ("research/lean/Other.lean", "lean_result_invalid") in issues
    assert ("research/lean/Main.lean", "lean_result_invalid") not in issues


def test_editing_the_proof_after_recording_a_pass_invalidates_it(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    source = _sound(root)

    source.write_text(
        "theorem argus_add_comm (a b : Nat) : a + b = b + a := by\n  admit\n",
        encoding="utf-8",
    )

    codes = _codes(root)
    assert "lean_result_stale" in codes
    assert "lean_proof_hole" in codes


def test_a_corrupt_recorded_result_is_reported(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _write_source(root)
    _write_fidelity(root)
    (_lean_dir(root) / "lean_check.json").write_text("{not json", encoding="utf-8")

    assert "lean_result_unreadable" in _codes(root)


def test_a_lean_source_with_no_result_at_all_blocks(tmp_path: Path) -> None:
    """Review point 4: a claim with nothing behind it blocks at every profile."""
    for profile in ("explore", "develop", "certify"):
        root = _project(tmp_path / profile, profile=profile)
        _write_source(root)
        _write_fidelity(root)

        assert "lean_result_missing" in _codes(root), profile
        joined = " ".join(stage_completion_issues("solve", root))
        assert "proves nothing yet" in joined, profile
        assert "lean_evidence verify" in joined, profile


# -- environment failures: distinguished in words, not in verdict -----------

def test_a_missing_toolchain_blocks_while_saying_it_is_an_environment_gap(
    tmp_path: Path,
) -> None:
    """Inverted from the first round, which asserted this did *not* block.

    The old reasoning was that a run with no compiler says nothing about the
    mathematics, which is true — but "says nothing" is not "says yes", and an
    unverified formalization is not evidence. The escape hatch is not
    committing a `.lean` file you cannot check.
    """
    root = _project(tmp_path)
    _write_source(root)
    _write_fidelity(root)
    _write_result(
        root,
        status="unavailable",
        exit_code=None,
        audit_exit_code=None,
        command=[],
        tools={"lean": {"available": False, "path": None, "version": ""}},
        stderr="lean executable is unavailable.",
    )

    report = validate_lean_evidence(root)
    assert {issue.code for issue in report.issues} == {
        "lean_unverified_toolchain_absent"
    }
    assert not report.sources[0].verified
    assert stage_completion_issues("solve", root) != ()

    # The distinction survives where it is useful: in what the reviewer reads.
    message = report.issues[0].message
    assert "environment gap, not a mathematical defect" in message
    assert "no Lean toolchain" in message


def test_a_missing_library_blocks_while_naming_the_library(
    tmp_path: Path,
) -> None:
    """Inverted from the first round. This host's real shape: no Mathlib.

    This is the case that decided the rule. Every serious formalization imports
    Mathlib, so treating a missing-Mathlib failure as excusable would have left
    the gate open in every scenario it was built for.
    """
    root = _project(tmp_path)
    _write_source(root, MATHLIB_THEOREM)
    _write_fidelity(
        root,
        "# Statement fidelity\n\n`argus_dvd_add` formalizes: if a divides b and "
        "a divides c then a divides b + c, over the integers, with no added "
        "assumptions.\n",
    )
    _write_result(
        root,
        status="type_error",
        exit_code=1,
        audit_exit_code=None,
        stdout=(
            "Main.lean:1:0: error: unknown module prefix 'Mathlib'\n\n"
            "No directory 'Mathlib' or file 'Mathlib.olean' in the search path "
            "entries:\n/home/u/.elan/toolchains/leanprover--lean4---v4.33.0/lib/lean\n"
        ),
    )

    report = validate_lean_evidence(root)
    assert {issue.code for issue in report.issues} == {
        "lean_unverified_missing_dependency"
    }
    assert not report.sources[0].verified
    assert stage_completion_issues("solve", root) != ()

    message = report.issues[0].message
    assert "Mathlib is not in the search path" in message
    assert "environment gap, not a mathematical defect" in message
    # And it is worded differently from a proof the compiler actually rejected.
    assert "lean_compile_failed" not in {issue.code for issue in report.issues}


def test_an_unrunnable_axiom_audit_blocks_separately(tmp_path: Path) -> None:
    """The compiler ran; the audit did not. Neither a pass nor a proof defect."""
    root = _project(tmp_path)
    _write_source(root)
    _write_fidelity(root)
    _write_result(
        root,
        status="unavailable",
        exit_code=0,
        audit_exit_code=2,
        stderr="audit could not elaborate",
    )

    assert "lean_unverified_audit_failed" in _codes(root)
    assert "unaudited proof is not evidence" in " ".join(
        stage_completion_issues("solve", root)
    )


def test_a_genuine_type_error_is_still_an_issue(tmp_path: Path) -> None:
    """The compiler reached the mathematics and rejected it."""
    root = _project(tmp_path)
    _write_source(root)
    _write_fidelity(root)
    _write_result(
        root,
        status="type_error",
        exit_code=1,
        audit_exit_code=None,
        stdout="Main.lean:1:44: error: application type mismatch\n",
    )

    assert classify_environment_failure(
        json.loads((_lean_dir(root) / "lean_check.json").read_text())
    ) == ""
    assert "lean_compile_failed" in _codes(root)


def test_a_proof_hole_is_never_excused_by_the_environment() -> None:
    assert classify_environment_failure({
        "status": "proof_hole",
        "stdout": "unknown module prefix 'Mathlib'",
    }) == ""


def test_the_toolchain_probe_reports_path_and_version_per_tool() -> None:
    tools = audit_lean_tools()

    assert set(tools) == {"lean", "lake", "elan"}
    for info in tools.values():
        assert set(info) == {"available", "path", "version"}
        assert isinstance(info["available"], bool)
    # `available` means the executable resolved, not that it answered: elan
    # downloads a toolchain on first use and the version probe can time out.
    for info in tools.values():
        if not info["available"]:
            assert info["path"] is None


# -- discovery must be complete or say that it is not -----------------------

def test_a_proof_hidden_in_a_build_directory_is_still_found(
    tmp_path: Path,
) -> None:
    """Review point 9: the old skip list made `build/` a hiding place."""
    root = _project(tmp_path)
    for relative in ("build/Hidden.lean", ".secret/Hidden.lean", "Mathlib/Mine.lean"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("theorem argus_hidden : True := by\n  sorry\n", encoding="utf-8")

    found = {p.name for p in discover_lean_sources(root)}
    assert found == {"Hidden.lean", "Mine.lean"}
    assert "lean_proof_hole" in _codes(root)


def test_too_many_sources_stops_the_sweep_and_says_so(tmp_path: Path) -> None:
    """Review point 8: checking the first N of an unknown number checks none."""
    root = _project(tmp_path)
    directory = _lean_dir(root)
    for index in range(MAX_DISCOVERED_SOURCES + 5):
        (directory / f"F{index}.lean").write_text(
            "theorem argus_ok : True := trivial\n", encoding="utf-8"
        )

    codes = _codes(root)
    assert "lean_discovery_truncated" in codes
    joined = " ".join(stage_completion_issues("solve", root))
    assert "the remainder is unchecked" in joined


def test_a_symlinked_directory_is_reported_rather_than_silently_skipped(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    _sound(root)
    outside = tmp_path.parent / "outside-lean"
    outside.mkdir(exist_ok=True)
    (outside / "Sneaky.lean").write_text(
        "theorem argus_sneaky : True := by\n  sorry\n", encoding="utf-8"
    )
    _symlink_or_skip(root / "linked", outside, target_is_directory=True)

    codes = _codes(root)
    assert "lean_discovery_incomplete" in codes
    assert stage_completion_issues("solve", root) != ()


def test_a_symlinked_source_outside_the_project_is_reported(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _sound(root)
    outside = tmp_path.parent / "outside-source"
    outside.mkdir(exist_ok=True)
    external = outside / "External.lean"
    external.write_text("theorem argus_ext : True := trivial\n", encoding="utf-8")
    _symlink_or_skip(_lean_dir(root) / "Linked.lean", external)

    assert "lean_source_external" in _codes(root)


def test_a_symlinked_source_inside_the_project_is_followed_once(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    source = _sound(root)
    _symlink_or_skip(_lean_dir(root) / "Alias.lean", source)

    # Same file twice is one proof, not two, and it still passes.
    assert discover_lean_sources(root) == (source,)
    assert validate_lean_evidence(root).issues == ()


# -- caching: fast, but never a stale pass ----------------------------------

def test_repeated_checks_do_not_repeat_the_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus_skill.verticals.math.lean_evidence as module

    root = _project(tmp_path)
    _sound(root)
    module._CACHE.clear()

    calls: list[Path] = []
    real = module.validate_lean_evidence
    monkeypatch.setattr(
        module,
        "validate_lean_evidence",
        lambda r, **kw: (calls.append(Path(str(r))), real(r, **kw))[1],
    )

    assert module.lean_evidence_issues(root) == ()
    assert module.lean_evidence_issues(root) == ()
    assert len(calls) == 1


def test_the_cache_cannot_be_fooled_by_restoring_the_timestamp(
    tmp_path: Path,
) -> None:
    """Review point 5: the memo keyed on size and mtime, both forgeable.

    The replacement proof is written to the exact byte length of the original
    and `os.utime` puts the timestamp back, so every piece of metadata the old
    key looked at is unchanged. Only the content differs.
    """
    import argus_skill.verticals.math.lean_evidence as module

    root = _project(tmp_path)
    source = _sound(root)
    module._CACHE.clear()
    assert module.lean_evidence_issues(root) == ()

    before = source.stat()
    original = source.read_text(encoding="utf-8")
    forged = "theorem argus_add_comm (a b : Nat) : a + b = b + a := by sorry\n"
    forged = forged.rstrip("\n").ljust(len(original) - 1, " ") + "\n"
    source.write_text(forged, encoding="utf-8")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))

    # Every metadata field the old cache key used is identical.
    after = source.stat()
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns

    issues = module.lean_evidence_issues(root)
    assert issues != ()
    assert any("proof holes" in issue for issue in issues)


def test_the_cache_notices_a_fidelity_document_above_the_source(
    tmp_path: Path,
) -> None:
    """Review point 6: an ancestor document was consulted but never keyed."""
    import argus_skill.verticals.math.lean_evidence as module

    root = _project(tmp_path)
    _write_source(root)
    _write_result(root)
    ancestor = root / "research" / "statement_fidelity.md"
    ancestor.write_text(FIDELITY, encoding="utf-8")
    module._CACHE.clear()

    assert module.lean_evidence_issues(root) == ()

    # Gutted afterwards; the verdict depended on it, so it must be re-derived.
    stamp = ancestor.stat()
    ancestor.write_text("# Statement fidelity\n", encoding="utf-8")
    os.utime(ancestor, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    assert "lean_fidelity_empty" in {
        issue.code for issue in validate_lean_evidence(root).issues
    }
    assert module.lean_evidence_issues(root) != ()


def test_the_cache_notices_a_replaced_result(tmp_path: Path) -> None:
    import argus_skill.verticals.math.lean_evidence as module

    root = _project(tmp_path)
    _sound(root)
    module._CACHE.clear()
    assert module.lean_evidence_issues(root) == ()

    result = _lean_dir(root) / "lean_check.json"
    stamp = result.stat()
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["status"] = "type_error"
    payload["exit_code"] = 1
    payload["audit_exit_code"] = None
    payload["stdout"] = "Main.lean:1:1: error: application type mismatch\n"
    result.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(result, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    assert module.lean_evidence_issues(root) != ()


# -- the CLI ----------------------------------------------------------------

def test_check_reports_ok_on_sound_evidence(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path)
    _sound(root)

    assert main(["check", "--project-root", str(root)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert len(payload["verified"]) == 1


def test_check_fails_and_names_the_defect(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path)
    _write_source(root, "theorem argus_bad : True := by\n  sorry\n")

    assert main(["check", "--project-root", str(root)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert {"lean_proof_hole", "lean_fidelity_missing", "lean_result_missing"} <= {
        issue["code"] for issue in payload["issues"]
    }


def test_audit_prints_the_hosts_toolchain(capsys) -> None:
    assert main(["audit"]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {
        "lean",
        "lake",
        "elan",
        "mathlib_workspace",
    }


def test_verify_refuses_to_let_fidelity_be_the_source_itself(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    source = _write_source(root)

    # Lean can only check the statement you wrote; the document saying what it
    # was meant to say has to be a different artifact.
    with pytest.raises(ValueError, match="must be distinct"):
        verify_lean_source(source, statement_fidelity=source)


# -- against the real compiler ----------------------------------------------

@requires_lean
@pytest.mark.integration
def test_verify_records_a_real_pass_that_the_gate_then_accepts(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    source = _write_source(root)
    fidelity = _write_fidelity(root)

    result = verify_lean_source(
        source,
        statement_fidelity=fidelity,
        timeout_seconds=120.0,
    )

    assert result["status"] == "success", result
    assert result["audit_exit_code"] == 0
    assert result["source_sha256"]
    assert result["environment_failure"] == ""
    assert (_lean_dir(root) / "compile.log").is_file()

    # A real run satisfies the schema the gate enforces; nothing is hand-fixed.
    assert validate_lean_evidence(root).issues == ()
    assert stage_completion_issues("solve", root) == ()


@requires_lean
@pytest.mark.integration
def test_verify_records_a_real_failure_that_the_gate_then_blocks(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    source = _write_source(
        root,
        "theorem argus_add_comm (a b : Nat) : a + b = b + a := Nat.mul_comm a b\n",
    )
    fidelity = _write_fidelity(root)

    result = verify_lean_source(
        source,
        statement_fidelity=fidelity,
        timeout_seconds=120.0,
    )

    assert result["status"] in {"type_error", "syntax_error"}, result
    assert result["environment_failure"] == ""
    assert "lean_compile_failed" in _codes(root)
    assert stage_completion_issues("solve", root) != ()


@requires_lean
@pytest.mark.integration
def test_a_real_missing_mathlib_blocks_and_is_named_as_such(
    tmp_path: Path,
) -> None:
    """Inverted from the first round; only meaningful on a host without Mathlib."""
    root = _project(tmp_path)
    source = _write_source(root, MATHLIB_THEOREM)
    fidelity = _write_fidelity(
        root,
        "# Statement fidelity\n\n`argus_dvd_add` formalizes: if a divides b and "
        "a divides c then a divides b + c over the integers, no added "
        "assumptions.\n",
    )

    result = verify_lean_source(
        source,
        statement_fidelity=fidelity,
        timeout_seconds=180.0,
    )

    if result["status"] == "success":
        pytest.skip("this host has Mathlib, so there is no missing dependency")

    assert result["environment_failure"] == "missing_dependency", result
    assert "lean_unverified_missing_dependency" in _codes(root)
    assert stage_completion_issues("solve", root) != ()


@pytest.mark.integration
def test_a_host_without_lean_records_unavailable_and_still_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The toolchain-absent path, forced so it runs on any host."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    monkeypatch.setenv("ELAN_HOME", str(tmp_path / "no-elan"))
    _set_home(monkeypatch, tmp_path / "no-home")

    root = _project(tmp_path)
    source = _write_source(root)
    fidelity = _write_fidelity(root)

    result = verify_lean_source(source, statement_fidelity=fidelity)

    assert result["status"] == "unavailable"
    assert result["environment_failure"] == "toolchain_absent"
    report = validate_lean_evidence(root)
    assert {issue.code for issue in report.issues} == {
        "lean_unverified_toolchain_absent"
    }
    assert not report.sources[0].verified


# -- which toolchain the verify step reaches for -----------------------------
#
# `run_lean_check` compiles through bare `lean` unless told otherwise, which is
# right for a primitive and wrong for a default. Serious formalization imports
# Mathlib, Mathlib is only on the search path through `lake env lean`, and an
# Engineer who follows the documented `verify` invocation passes no flags. So
# for the whole life of this module the recorded verdict on a host *with*
# Mathlib installed was `missing_dependency` — the one message that tells the
# reader to go install the library sitting on their disk. These fix the default
# and pin both overrides, since a decision made for the caller has to be
# refusable and has to say it was made.

def _fake_lake(tmp_path: Path) -> str:
    """Answers `--version`, `env lean ...`, and the axiom audit with success."""
    if os.name == "nt":
        script = tmp_path / "fake-lake.py"
        script.write_text("raise SystemExit(0)\n", encoding="utf-8")
        path = tmp_path / "fake-lake.cmd"
        path.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
        return str(path)
    path = tmp_path / "fake-lake"
    path.write_text(
        "#!/usr/bin/env python3\nraise SystemExit(0)\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return str(path)


def _set_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))


def _mathlib_workspace(home: Path) -> Path:
    workspace = home / ".local" / "share" / "argus-skill" / "mathlib"
    workspace.mkdir(parents=True)
    (workspace / "lakefile.toml").write_text('name = "mathlib"\n', encoding="utf-8")
    return workspace.resolve()


def test_verify_reaches_for_an_installed_mathlib_without_being_asked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = _mathlib_workspace(home)
    _set_home(monkeypatch, home)
    monkeypatch.delenv("ARGUS_SKILL_MATHLIB_WORKSPACE", raising=False)
    root = _project(tmp_path)
    source = _write_source(root, MATHLIB_THEOREM)
    fidelity = _write_fidelity(root)

    result = verify_lean_source(
        source,
        statement_fidelity=fidelity,
        lake_bin=_fake_lake(tmp_path),
    )

    assert result["tool"] == "lake"
    assert result["cwd"] == str(workspace)
    # Recorded, not merely done: a compile whose search path was chosen for the
    # caller has to name the workspace it was given.
    assert result["lake_workspace"] == str(workspace)
    assert json.loads(
        (_lean_dir(root) / "lean_check.json").read_text(encoding="utf-8")
    )["lake_workspace"] == str(workspace)


def test_verify_stays_on_bare_lean_when_no_workspace_applies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_home(monkeypatch, tmp_path / "no-home")
    monkeypatch.delenv("ARGUS_SKILL_MATHLIB_WORKSPACE", raising=False)
    root = _project(tmp_path)
    source = _write_source(root)
    fidelity = _write_fidelity(root)

    result = verify_lean_source(
        source,
        statement_fidelity=fidelity,
        lean_bin=_fake_lake(tmp_path),
    )

    assert result["tool"] == "lean"
    assert result["lake_workspace"] == ""


def test_verify_honours_an_explicit_refusal_to_use_lake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    _mathlib_workspace(home)
    _set_home(monkeypatch, home)
    monkeypatch.delenv("ARGUS_SKILL_MATHLIB_WORKSPACE", raising=False)
    root = _project(tmp_path)
    source = _write_source(root)
    fidelity = _write_fidelity(root)

    result = verify_lean_source(
        source,
        statement_fidelity=fidelity,
        lean_bin=_fake_lake(tmp_path),
        use_lake=False,
    )

    assert result["tool"] == "lean"
    assert result["lake_workspace"] == ""


def test_verify_cli_defaults_to_lake_and_takes_no_lake_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    workspace = _mathlib_workspace(home)
    _set_home(monkeypatch, home)
    monkeypatch.delenv("ARGUS_SKILL_MATHLIB_WORKSPACE", raising=False)
    root = _project(tmp_path)
    source = _write_source(root, MATHLIB_THEOREM)
    fidelity = _write_fidelity(root)
    fake = _fake_lake(tmp_path)
    invocation = [
        "verify",
        str(source),
        "--statement-fidelity",
        str(fidelity),
        "--lean-bin",
        fake,
        "--lake-bin",
        fake,
    ]

    assert main(invocation) == 0
    assert json.loads(capsys.readouterr().out)["cwd"] == str(workspace)

    assert main([*invocation, "--no-lake"]) == 0
    forced = json.loads(capsys.readouterr().out)
    assert forced["tool"] == "lean"
    assert forced["lake_workspace"] == ""


def test_a_missing_library_message_says_where_it_was_looked_for(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Provide the library" is advice only to someone who knows the three places."""
    _set_home(monkeypatch, tmp_path / "no-home")
    monkeypatch.delenv("ARGUS_SKILL_MATHLIB_WORKSPACE", raising=False)
    root = _project(tmp_path)
    _write_source(root, MATHLIB_THEOREM)
    _write_fidelity(
        root,
        "# Statement fidelity\n\n`argus_dvd_add` formalizes: a divides b and a "
        "divides c implies a divides b + c over the integers. No added "
        "assumptions.\n",
    )
    _write_result(
        root,
        status="type_error",
        exit_code=1,
        stderr="error: unknown module prefix 'Mathlib'\n",
        audit_exit_code=None,
    )

    message = _message(root, "lean_unverified_missing_dependency")

    assert str(tmp_path / "no-home" / ".local" / "share" / "argus-skill" / "mathlib") in message
    assert "ARGUS_SKILL_MATHLIB_WORKSPACE" in message
    assert "lakefile.toml" in message


def test_a_present_but_unused_library_is_named_as_the_flags_doing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same failure with Mathlib installed is a different problem entirely."""
    home = tmp_path / "home"
    workspace = _mathlib_workspace(home)
    _set_home(monkeypatch, home)
    monkeypatch.delenv("ARGUS_SKILL_MATHLIB_WORKSPACE", raising=False)
    root = _project(tmp_path)
    _write_source(root, MATHLIB_THEOREM)
    _write_fidelity(
        root,
        "# Statement fidelity\n\n`argus_dvd_add` formalizes: a divides b and a "
        "divides c implies a divides b + c over the integers. No added "
        "assumptions.\n",
    )
    _write_result(
        root,
        status="type_error",
        exit_code=1,
        stderr="error: unknown module prefix 'Mathlib'\n",
        audit_exit_code=None,
    )

    message = _message(root, "lean_unverified_missing_dependency")

    assert str(workspace) in message
    assert "--no-lake" in message
    # Telling this reader to install Mathlib is what the old wording did.
    assert "install Mathlib" not in message


def test_audit_answers_whether_import_mathlib_would_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    workspace = _mathlib_workspace(home)
    _set_home(monkeypatch, home)
    monkeypatch.delenv("ARGUS_SKILL_MATHLIB_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)

    assert main(["audit"]) == 0
    reported = json.loads(capsys.readouterr().out)["mathlib_workspace"]

    assert reported["resolved"] == str(workspace)
    # An empty answer has to come with where it looked, or it is unactionable.
    assert any("ARGUS_SKILL_MATHLIB_WORKSPACE" in place for place in reported["searched"])


# -- the window the compiler is reading in -----------------------------------
#
# A certificate exists to bind one compiler verdict to the exact bytes that
# were compiled. For as long as this module existed it hashed the source
# *after* `run_lean_check` returned, so an edit landing inside the compile
# window produced a record carrying the new text's digest and the old text's
# verdict — and `lean_evidence check` then confirmed the record was current,
# because the recorded digest and the file on disk agreed perfectly. The window
# is not hypothetical: `prepare_canonical_lean_artifacts` snapshots the source
# only when it is not already `Main.lean`, and the documented invocation
# compiles `research/lean/Main.lean` in place.
#
# The race is stubbed rather than timed. A test that depends on beating a real
# compiler is a test that passes on a slow host and says nothing on a fast one.

SWAPPED_THEOREM = (
    "theorem argus_add_comm (n : Nat) : n = n + 1 := Nat.add_comm n 1\n"
)
SWAPPED_FIDELITY = (
    "# Statement fidelity\n\n"
    "`argus_add_comm` formalizes: every natural number equals its own successor.\n"
    "Objects: natural numbers. Quantifiers: universal over n.\n"
    "Hypotheses: none. Conclusion: n = n + 1. Added assumptions: none.\n"
)


def _compiler_that_edits(path: Path, text: str):
    """A compile that answers about what it read, then finds the file rewritten.

    Deliberately the true ordering of the bug: the real checker runs against
    the bytes that were there, and only then does the editor's write land. So
    the returned verdict genuinely describes the old text, which is what makes
    publishing it against the new text's digest a lie rather than a mismatch.
    """
    real = lean_check.run_lean_check

    def compile_then_edit(source, **kwargs):
        result = real(source, **kwargs)
        path.write_text(text, encoding="utf-8")
        return result

    return compile_then_edit


def _bare_lean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A compiler that succeeds on any host, with no Mathlib in reach."""
    _set_home(monkeypatch, tmp_path / "no-home")
    monkeypatch.delenv("ARGUS_SKILL_MATHLIB_WORKSPACE", raising=False)
    return _fake_lake(tmp_path)


def test_verify_records_the_digests_of_the_bytes_that_were_compiled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary path: both digests name the files as the compiler saw them."""
    lean_bin = _bare_lean(tmp_path, monkeypatch)
    root = _project(tmp_path)
    source = _write_source(root)
    fidelity = _write_fidelity(root)

    result = verify_lean_source(
        source, statement_fidelity=fidelity, lean_bin=lean_bin
    )

    assert result["source_sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    assert result["statement_fidelity_sha256"] == hashlib.sha256(
        fidelity.read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()
    recorded = json.loads(
        (_lean_dir(root) / "lean_check.json").read_text(encoding="utf-8")
    )
    assert recorded["source_sha256"] == result["source_sha256"]
    assert recorded["statement_fidelity_sha256"] == result["statement_fidelity_sha256"]
    assert not {"lean_result_stale", "lean_fidelity_changed"} & _codes(root)


def test_a_source_edited_under_the_compiler_publishes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The certificate this run would have written is the forgery, so there is none."""
    lean_bin = _bare_lean(tmp_path, monkeypatch)
    root = _project(tmp_path)
    source = _write_source(root)
    fidelity = _write_fidelity(root)
    monkeypatch.setattr(
        lean_check,
        "run_lean_check",
        _compiler_that_edits(source, SWAPPED_THEOREM),
    )

    with pytest.raises(CompiledArtifactChangedError) as raised:
        verify_lean_source(source, statement_fidelity=fidelity, lean_bin=lean_bin)

    message = str(raised.value)
    assert "the Lean source" in message
    assert str(source) in message
    assert "run verify again" in message
    # Not a weakened certificate, not an unverified one: none at all.
    assert not (_lean_dir(root) / "lean_check.json").exists()
    assert not (_lean_dir(root) / "compile.log").exists()
    # And the edit itself is not blessed by the absence of a record.
    assert "lean_result_missing" in _codes(root)


def test_a_fidelity_note_rewritten_under_the_compiler_publishes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unchecked half of the argument has the same binding, so the same rule.

    A note rewritten after the compiler answered re-labels a proof of one thing
    as a proof of another, and nothing downstream can tell: the recorded digest
    would match the new note exactly, so `lean_fidelity_changed` never fires.
    """
    lean_bin = _bare_lean(tmp_path, monkeypatch)
    root = _project(tmp_path)
    source = _write_source(root)
    fidelity = _write_fidelity(root)
    monkeypatch.setattr(
        lean_check,
        "run_lean_check",
        _compiler_that_edits(fidelity, SWAPPED_FIDELITY),
    )

    with pytest.raises(CompiledArtifactChangedError) as raised:
        verify_lean_source(source, statement_fidelity=fidelity, lean_bin=lean_bin)

    message = str(raised.value)
    assert "statement_fidelity.md" in message
    # The proof held still; saying otherwise would send the reader to the wrong file.
    assert "the Lean source" not in message
    assert not (_lean_dir(root) / "lean_check.json").exists()
    assert not (_lean_dir(root) / "compile.log").exists()


def test_a_raced_re_verification_leaves_the_previous_certificate_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refusing must not half-write over what an earlier honest run recorded.

    The earlier record stays exactly as it was, which is also what makes the
    outcome fail closed: it describes the text that used to be there, the edit
    left different text, and the gate reports the pair as stale rather than
    reporting nothing.
    """
    lean_bin = _bare_lean(tmp_path, monkeypatch)
    root = _project(tmp_path)
    source = _write_source(root)
    fidelity = _write_fidelity(root)
    verify_lean_source(source, statement_fidelity=fidelity, lean_bin=lean_bin)
    before = (_lean_dir(root) / "lean_check.json").read_bytes()
    log_before = (_lean_dir(root) / "compile.log").read_bytes()
    monkeypatch.setattr(
        lean_check,
        "run_lean_check",
        _compiler_that_edits(source, SWAPPED_THEOREM),
    )

    with pytest.raises(CompiledArtifactChangedError):
        verify_lean_source(source, statement_fidelity=fidelity, lean_bin=lean_bin)

    assert (_lean_dir(root) / "lean_check.json").read_bytes() == before
    assert (_lean_dir(root) / "compile.log").read_bytes() == log_before
    assert "lean_result_stale" in _codes(root)
    assert stage_completion_issues("solve", root) != ()


def test_a_file_deleted_under_the_compiler_is_the_same_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gone is a kind of changed; a digest that cannot be re-taken is not a match."""
    lean_bin = _bare_lean(tmp_path, monkeypatch)
    root = _project(tmp_path)
    source = _write_source(root)
    fidelity = _write_fidelity(root)
    real = lean_check.run_lean_check

    def compile_then_delete(target, **kwargs):
        result = real(target, **kwargs)
        fidelity.unlink()
        return result

    monkeypatch.setattr(lean_check, "run_lean_check", compile_then_delete)

    with pytest.raises(CompiledArtifactChangedError, match="could no longer be read"):
        verify_lean_source(source, statement_fidelity=fidelity, lean_bin=lean_bin)

    assert not (_lean_dir(root) / "lean_check.json").exists()


def test_the_cli_refuses_a_raced_run_rather_than_reporting_it_unverified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    """`unverified` means the environment could not answer. It answered."""
    lean_bin = _bare_lean(tmp_path, monkeypatch)
    root = _project(tmp_path)
    source = _write_source(root)
    fidelity = _write_fidelity(root)
    monkeypatch.setattr(
        lean_check,
        "run_lean_check",
        _compiler_that_edits(source, SWAPPED_THEOREM),
    )

    assert main([
        "verify",
        str(source),
        "--statement-fidelity",
        str(fidelity),
        "--lean-bin",
        lean_bin,
    ]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "changed while the compiler was running" in payload["error"]
    assert "status" not in payload
    assert not (_lean_dir(root) / "lean_check.json").exists()


def test_a_raced_run_records_nothing_in_the_claim_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal has to reach the ledger, not only the artifact directory.

    ``verify --claim`` publishes in three places: the result beside the source,
    the certificate archived under `research/lean/certificates/`, and the
    evidence record that is the only way a claim reaches `closed_kernel`. A
    source edited while the compiler was reading it must produce none of the
    three — a record citing a compile that describes different text is exactly
    the citation the archive exists to make trustworthy.
    """
    from argus_skill.proof_ledger import load_state
    from argus_skill.verticals.math.math_state import main as state_main

    lean_bin = _bare_lean(tmp_path, monkeypatch)
    root = _project(tmp_path)
    source = _write_source(root)
    fidelity = _write_fidelity(root)
    state_main([
        "context", "--project-root", str(root),
        "--id", "ctx", "--statement", "Natural numbers.",
    ])
    state_main([
        "claim", "--project-root", str(root),
        "--id", "C1", "--context", "ctx",
        "--statement", "addition of naturals is commutative",
        "--formal-file", str(source),
    ])
    monkeypatch.setattr(
        lean_check,
        "run_lean_check",
        _compiler_that_edits(source, SWAPPED_THEOREM),
    )

    assert main([
        "verify",
        str(source),
        "--statement-fidelity",
        str(fidelity),
        "--claim",
        "C1",
        "--project-root",
        str(root),
        "--lean-bin",
        lean_bin,
    ]) == 2

    assert not (_lean_dir(root) / "lean_check.json").exists()
    assert not (_lean_dir(root) / "certificates").exists()
    assert not load_state(root).evidence
