"""The write path into the kernel, and the tier an agent cannot type.

The kernel could express a project's beliefs and derive what they added up to,
and nothing could put anything into it. This covers the module that closes that
gap, and it is mostly adversarial for one reason: ``MathState.add_evidence``
accepts every tier, because a legitimate producer of each has to reach it. The
tier is therefore decided by the command surface, and the property that makes
the whole schema mean anything is that an agent's command cannot choose one
that confers kernel status.

That property is asserted three ways below — as the constant, as the behaviour
of the funnel every command writes through, and as a sweep of the source that
fails if a later flag routes around either. The third is the one that matters
after this PR: the first two would still pass if somebody added ``--tier`` next
to them.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus.proof_ledger import (
    ClaimStatus,
    ClaimVersion,
    EvidenceTier,
    MathState,
    MathStateError,
    RouteStatus,
    SubjectKind,
    SubjectRef,
    Verdict,
    load_state,
)
from argus.tools.lean_check import audit_lean_tools
from argus.verticals.math import math_state
from argus.verticals.math.lean_evidence import validate_lean_evidence
from argus.verticals.math.math_state import (
    AGENT_WRITABLE_TIERS,
    main,
    record_lean_evidence,
)

REPO_ROOT = Path(__file__).parents[2]
MODULE = REPO_ROOT / "argus" / "verticals" / "math" / "math_state.py"

THEOREM = (
    "theorem argus_add_comm (a b : Nat) : a + b = b + a := Nat.add_comm a b\n"
)
FIDELITY = (
    "# Statement fidelity\n\n"
    "`argus_add_comm` formalizes: for all natural numbers a and b, a + b = b + a.\n"
    "Objects: natural numbers. Quantifiers: universal over a and b.\n"
    "Hypotheses: none. Conclusion: commutativity of addition.\n"
)

requires_lean = pytest.mark.skipif(
    not audit_lean_tools().get("lean", {}).get("available"),
    reason="no Lean toolchain on this host",
)


# -- fixtures ---------------------------------------------------------------

def _run(root: Path, *argv: str) -> tuple[int, dict]:
    """One command, as the Engineer types it, with its JSON parsed."""
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main([argv[0], "--project-root", str(root), *argv[1:]])
    return code, json.loads(buffer.getvalue())


def _lean_dir(root: Path) -> Path:
    path = root / "research" / "lean"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _source(root: Path, text: str = THEOREM) -> Path:
    path = _lean_dir(root) / "Main.lean"
    path.write_text(text, encoding="utf-8")
    return path


def _fidelity(root: Path, text: str = FIDELITY) -> Path:
    path = _lean_dir(root) / "statement_fidelity.md"
    path.write_text(text, encoding="utf-8")
    return path


def _result(root: Path, **overrides) -> dict:
    """A complete, twice-hash-stamped success, written where verify puts it.

    Synthesized rather than compiled so the refusals below can be provoked one
    at a time; the end-to-end test at the bottom runs the real toolchain.
    """
    source = _lean_dir(root) / "Main.lean"
    note = _lean_dir(root) / "statement_fidelity.md"
    payload = {
        "schema_version": 1,
        "status": "success",
        "source": str(source),
        "tool": "lean",
        "tools": {
            "lean": {
                "available": True,
                "path": "/usr/bin/lean",
                "version": "Lean (version 4.34.0-rc1, x86_64-unknown-linux-gnu, Release)",
            }
        },
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
        "statement_fidelity": str(note),
        "statement_fidelity_sha256": hashlib.sha256(
            note.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest(),
    }
    payload.update(overrides)
    (_lean_dir(root) / "lean_check.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return payload


def _proved_project(root: Path) -> Path:
    """A claim whose formalization has compiled — the state before recording."""
    _source(root)
    _fidelity(root)
    _result(root)
    _run(root, "context", "--id", "ctx", "--statement", "Natural numbers.")
    _run(
        root,
        "claim",
        "--id",
        "C1",
        "--context",
        "ctx",
        "--statement",
        "addition of naturals is commutative",
        "--formal-file",
        str(_lean_dir(root) / "Main.lean"),
    )
    return _lean_dir(root) / "Main.lean"


# -- the round trip the Engineer is told to perform -------------------------

def test_a_context_claim_route_assumption_and_judgement_all_read_back(
    tmp_path: Path,
) -> None:
    """The definition of a usable write path: what went in comes back out.

    Every previous PR could describe this state and none could produce it, so
    this is the first test in the repository that exercises the kernel the way
    a run reaches it — through commands rather than through constructors.
    """
    assert _run(tmp_path, "context", "--id", "ctx", "--statement", "Fix n : Nat.",
                "--define", "even=n = 2k for some k")[0] == 0
    for claim_id, text in (("C1", "n + n is even"), ("L1", "2 divides n + n")):
        assert _run(tmp_path, "claim", "--id", claim_id, "--context", "ctx",
                    "--statement", text)[0] == 0
    assert _run(tmp_path, "route", "--id", "R1", "--goal", "C1",
                "--obligation", "L1")[0] == 0
    assert _run(tmp_path, "assume", "--by", "engineer:you", "--claim", "C1", "--id", "RH",
                "--statement", "The Riemann Hypothesis",
                "--source", "Riemann 1859")[0] == 0
    assert _run(tmp_path, "judge", "--claim", "C1", "--verdict", "supports",
                "--by", "reviewer:alice")[0] == 0

    code, payload = _run(tmp_path, "show", "--claim", "C1")
    assert code == 0
    claim = payload["claim"]
    assert claim["status"] == ClaimStatus.SUPPORTED.value
    assert claim["support"] == {"judgement": ["reviewer:alice"]}
    assert [item["assumption_id"] for item in claim["standing_on"]] == ["RH"]
    assert [route["route_id"] for route in claim["routes"]] == ["R1"]
    assert _run(tmp_path, "check")[0] == 0


def test_a_citation_records_the_proposition_and_reports_that_nobody_checked_it(
    tmp_path: Path,
) -> None:
    """The citation the reviewer can act on names a theorem, not a paper.

    And the state it starts in is ``unchecked`` rather than absent: the whole
    reason to record ``source_id`` and ``locator`` is that something can be sent
    to look them up, which only means anything if not having looked is visible.
    """
    assert _run(tmp_path, "context", "--id", "ctx", "--statement", "Fix n : Nat.")[0] == 0
    assert _run(tmp_path, "claim", "--id", "C1", "--context", "ctx",
                "--statement", "n + n is even")[0] == 0
    assert _run(tmp_path, "assume", "--by", "engineer:you", "--claim", "C1", "--id", "RH",
                "--statement", "The Riemann Hypothesis",
                "--source", "Titchmarsh, The Theory of the Riemann Zeta-function",
                "--source-id", "doi:10.1093/oso/9780198533696.001.0001",
                "--locator", "Theorem 14.2")[0] == 0

    code, payload = _run(tmp_path, "show", "--claim", "C1")
    assert code == 0
    assert payload["claim"]["citations"] == [
        {
            "assumption_id": "RH",
            "status": "unchecked",
            "cited_proposition": (
                "doi:10.1093/oso/9780198533696.001.0001 Theorem 14.2"
            ),
            "checked_by": [],
            "artifacts": [],
        }
    ]
    assert _run(tmp_path, "check")[0] == 0


def test_an_assumption_citing_prose_says_so_instead_of_queuing_a_lookup(
    tmp_path: Path,
) -> None:
    """No locator is a legitimate answer, and the command says which one it gave."""
    assert _run(tmp_path, "context", "--id", "ctx", "--statement", "Fix n : Nat.")[0] == 0
    assert _run(tmp_path, "claim", "--id", "C1", "--context", "ctx",
                "--statement", "n + n is even")[0] == 0
    code, payload = _run(tmp_path, "assume", "--by", "engineer:you", "--claim", "C1", "--id", "F",
                         "--statement", "the standard averaging bound",
                         "--source", "folklore, stated in seminar notes")
    assert code == 0
    assert "uncited rather than unchecked" in payload["citation"]

    _code, shown = _run(tmp_path, "show", "--claim", "C1")
    assert [item["status"] for item in shown["claim"]["citations"]] == ["uncited"]


def test_half_a_citation_is_refused_by_check_rather_than_read_as_uncited(
    tmp_path: Path,
) -> None:
    """A document with no proposition would look like a source nobody could check.

    That is the silent direction: ``assess_citation`` reads it as ``uncited``,
    so an agent that meant to make a dependency checkable would get a state
    saying it could not be, and no checker would ever be sent.
    """
    assert _run(tmp_path, "context", "--id", "ctx", "--statement", "Fix n : Nat.")[0] == 0
    assert _run(tmp_path, "claim", "--id", "C1", "--context", "ctx",
                "--statement", "n + n is even")[0] == 0
    assert _run(tmp_path, "assume", "--by", "engineer:you", "--claim", "C1", "--id", "RH",
                "--statement", "The Riemann Hypothesis",
                "--source", "Titchmarsh",
                "--source-id", "doi:10.1093/oso/9780198533696.001.0001")[0] == 0

    code, payload = _run(tmp_path, "check")
    assert code == 1
    assert [issue["code"] for issue in payload["issues"]] == ["citation_incomplete"]


def test_a_state_file_written_by_the_cli_is_the_one_the_kernel_reads(
    tmp_path: Path,
) -> None:
    """The commands and the library must not be two dialects of one file.

    Nothing else checks this: the CLI could round-trip through its own reader
    forever while the projector a later PR builds on ``load_state`` saw
    nothing.
    """
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Fix n : Nat.")
    _run(tmp_path, "claim", "--id", "C1", "--context", "ctx", "--statement", "P")
    state = load_state(tmp_path)
    assert [claim.claim_id for claim in state.current_claims()] == ["C1"]
    assert state.validate() == ()


# -- the tier an agent cannot type ------------------------------------------

def test_the_only_tier_a_command_may_take_from_an_agent_is_judgement() -> None:
    """Named, so widening the set is a visible edit to a test that says why."""
    assert AGENT_WRITABLE_TIERS == frozenset({EvidenceTier.JUDGEMENT})
    assert EvidenceTier.MECHANICAL not in AGENT_WRITABLE_TIERS
    assert EvidenceTier.COMPUTATIONAL not in AGENT_WRITABLE_TIERS
    assert EvidenceTier.LITERATURE not in AGENT_WRITABLE_TIERS


@pytest.mark.parametrize(
    "tier",
    [EvidenceTier.MECHANICAL, EvidenceTier.COMPUTATIONAL, EvidenceTier.LITERATURE],
)
def test_the_agent_funnel_refuses_every_tier_it_did_not_check(
    tier: EvidenceTier,
) -> None:
    """``mechanical`` and ``computational`` confer status; ``literature`` claims
    independence from the model, which the model cannot assert about itself."""
    state = MathState()
    with pytest.raises(MathStateError) as caught:
        math_state._agent_evidence(
            state,
            subject=SubjectRef(SubjectKind.CLAIM, "C1", "a" * 64),
            tier=tier,
            verdict=Verdict.SUPPORTS,
            produced_by="agent",
        )
    assert "cannot be recorded from a command line" in str(caught.value)
    assert state.evidence == []


def test_no_agent_command_reaches_add_evidence_except_through_the_funnel() -> None:
    """The invariant, rather than today's absence of a ``--tier`` flag.

    Both tests above would still pass if a later PR added ``--tier`` beside
    them: the constant would be untouched and ``_agent_evidence`` would still
    refuse, while a new subcommand called ``state.add_evidence`` directly. This
    reads the source instead, and fails the moment a second door exists.
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    callers: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_evidence"
            ):
                callers.add(function.name)
    assert callers == {"_append_evidence"}, (
        "every write of evidence must pass the tier gate; these functions call "
        f"MathState.add_evidence directly: {sorted(callers)}"
    )

    appenders = {
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_append_evidence"
    }
    # Three, and each one is a producer: the agent funnel (judgement only), the
    # compiler reader, and the citation checker — which archives the retrieved
    # passage before it records anything about it. A fourth name appearing here
    # is a new claim about who is allowed to establish something, and it should
    # cost an edit to this line.
    assert appenders == {
        "_agent_evidence",
        "record_citation_evidence",
        "record_lean_evidence",
    }


def test_a_kernel_tier_is_named_only_where_a_checker_was_run() -> None:
    """Each non-agent tier appears in one function, and that function checked.

    A sweep rather than an assertion about behaviour, because the failure being
    guarded is a future edit: a helper that writes ``MECHANICAL`` from anywhere
    that did not read a compiler's answer is the whole vulnerability, whatever
    it is called. ``LITERATURE`` is held to the same rule now that it has a
    producer — it may be named where the retrieval is archived, and nowhere
    else, so a convenience wrapper that records a citation verdict without a
    passage attached fails here rather than in review.
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    named: dict[str, set[str]] = {}
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "EvidenceTier"
                and node.attr in {"MECHANICAL", "COMPUTATIONAL", "LITERATURE"}
            ):
                named.setdefault(function.name, set()).add(node.attr)
    assert named == {
        "record_lean_evidence": {"MECHANICAL"},
        "record_citation_evidence": {"LITERATURE"},
    }


def test_the_agent_command_surface_offers_no_option_that_selects_a_tier() -> None:
    """Read off the parser, so a flag added anywhere in it is caught.

    ``--force``, ``--unsafe``, ``--override`` and friends are named too: the
    brief's rule is that there is no escape hatch, and an escape hatch does not
    have to be spelled ``--tier`` to be one.
    """
    forbidden = (
        "tier", "force", "unsafe", "override", "mechanical", "computational",
        "literature", "kernel", "trust", "skip", "no-verify", "admin",
    )
    parser = math_state._build_parser()
    subparsers = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert subparsers, "the sweep found no subcommands, so it proves nothing"

    offenders: list[str] = []
    for group in subparsers:
        for name, child in group.choices.items():
            for action in child._actions:
                for option in action.option_strings:
                    if any(word in option.lower() for word in forbidden):
                        offenders.append(f"{name} {option}")
                for choice in action.choices or ():
                    if str(choice).lower() in {
                        "mechanical", "computational", "literature"
                    }:
                        offenders.append(f"{name} {action.dest}={choice}")
    assert offenders == []

    judge = group.choices["judge"]
    verdicts = next(
        action.choices for action in judge._actions if action.dest == "verdict"
    )
    assert set(verdicts) == {item.value for item in Verdict}


def test_no_environment_variable_can_change_what_a_command_may_write() -> None:
    """An env var is the escape hatch that no ``--help`` output would show."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    reads = [
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"})
        or (isinstance(node, ast.Name) and node.id in {"environ", "getenv"})
    ]
    assert reads == []

    # And behaviourally, for the names somebody would reach for first.
    state = MathState()
    for name in ("ARGUS_SKILL_MATH_TIER", "ARGUS_SKILL_MATH_FORCE"):
        os.environ[name] = "mechanical"
    try:
        with pytest.raises(MathStateError):
            math_state._agent_evidence(
                state,
                subject=SubjectRef(SubjectKind.CLAIM, "C1", "a" * 64),
                tier=EvidenceTier.MECHANICAL,
                verdict=Verdict.SUPPORTS,
                produced_by="agent",
            )
    finally:
        for name in ("ARGUS_SKILL_MATH_TIER", "ARGUS_SKILL_MATH_FORCE"):
            del os.environ[name]


def test_a_judgement_cannot_reach_a_kernel_status_however_many_are_recorded(
    tmp_path: Path,
) -> None:
    """The tier gate would be pointless if the writable tier promoted anyway."""
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Fix n : Nat.")
    _run(tmp_path, "claim", "--id", "C1", "--context", "ctx", "--statement", "P",
         "--formal", "theorem p : True := trivial")
    for referee in ("alice", "bob", "carol", "dan", "erin"):
        assert _run(tmp_path, "judge", "--claim", "C1", "--verdict", "supports",
                    "--by", f"reviewer:{referee}")[0] == 0
    _, payload = _run(tmp_path, "show", "--claim", "C1")
    assert payload["claim"]["status"] == ClaimStatus.SUPPORTED.value
    assert len(payload["claim"]["support"]["judgement"]) == 5
    assert "caveats" not in payload["claim"]


def test_repeating_a_judgement_does_not_manufacture_a_second_producer(
    tmp_path: Path,
) -> None:
    """Retrying is how an autonomous loop recovers, and independence is counted.

    Without a derived id, an agent that ran the same command twice would turn
    one referee into two, which is exactly the reading ``support`` invites.
    """
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Fix n : Nat.")
    _run(tmp_path, "claim", "--id", "C1", "--context", "ctx", "--statement", "P")
    first = _run(tmp_path, "judge", "--claim", "C1", "--verdict", "supports",
                 "--by", "reviewer:alice")[1]
    second = _run(tmp_path, "judge", "--claim", "C1", "--verdict", "supports",
                  "--by", "reviewer:alice")[1]
    assert first["changed"] is True
    assert second["changed"] is False
    assert len(load_state(tmp_path).evidence) == 1

    # A different answer from the same referee is a different record.
    _run(tmp_path, "judge", "--claim", "C1", "--verdict", "inconclusive",
         "--by", "reviewer:alice")
    assert len(load_state(tmp_path).evidence) == 2


# -- concurrency ------------------------------------------------------------

def test_simultaneous_writers_do_not_lose_a_record(tmp_path: Path) -> None:
    """Every command is a read-modify-write over one JSON file.

    Real processes rather than threads, because the writers this serializes are
    separate agent invocations and a lock that only holds within one interpreter
    would pass a threaded test and lose writes in production.
    """
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Fix n : Nat.")
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "argus.verticals.math.math_state",
                "claim",
                "--project-root",
                str(tmp_path),
                "--id",
                f"C{index}",
                "--context",
                "ctx",
                "--statement",
                f"claim number {index}",
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        for index in range(12)
    ]
    for process in processes:
        _, errors = process.communicate(timeout=120)
        assert process.returncode == 0, errors.decode("utf-8", "replace")

    written = {claim.claim_id for claim in load_state(tmp_path).current_claims()}
    assert written == {f"C{index}" for index in range(12)}


def test_a_body_that_raises_inside_the_lock_publishes_nothing(
    tmp_path: Path,
) -> None:
    """A rejected write must not be half a write.

    The lock makes the read-modify-write atomic against other processes; this
    is the other half, and it is asserted against ``locked_state`` rather than
    against a command because no command today mutates and then refuses — they
    all validate first. That ordering is a property of nine functions and could
    change in any of them; this is a property of the one place they all write
    through.
    """
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Fix n : Nat.")
    _run(tmp_path, "claim", "--id", "C1", "--context", "ctx", "--statement", "P")
    before = math_state.state_path(tmp_path).read_bytes()

    with pytest.raises(RuntimeError):
        with math_state.locked_state(tmp_path) as state:
            state.add_claim(
                ClaimVersion(
                    claim_id="C2",
                    version=1,
                    context=state.latest_claim("C1").context,
                    natural_statement="written then abandoned",
                )
            )
            raise RuntimeError("the command decided to refuse")

    assert math_state.state_path(tmp_path).read_bytes() == before
    assert [claim.claim_id for claim in load_state(tmp_path).current_claims()] == ["C1"]

    # And the surface agrees: a refusal reports it and changes nothing.
    code, payload = _run(tmp_path, "route", "--id", "R1", "--goal", "C1",
                         "--obligation", "MISSING")
    assert code == 1
    assert payload["ok"] is False
    assert math_state.state_path(tmp_path).read_bytes() == before


def test_a_hand_edited_ledger_is_repaired_by_the_next_write_not_refused_by_it(
    tmp_path: Path,
) -> None:
    """State arrives by text editor as well as by command, and must stay usable.

    ``revise_claim`` demands a written reason for every carried assumption a new
    version drops, and "carried" walks the whole history — so a revision built
    from the last version's own list would be refused as a silent deletion on
    any claim whose ledger somebody damaged by hand. That turns one bad edit
    into a claim no command can touch again. Building each version from what the
    claim carries re-lists the inherited dependency instead, which is the repair
    the store documents.
    """
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Fix n : Nat.")
    _run(tmp_path, "claim", "--id", "C1", "--context", "ctx", "--statement", "P")
    _run(tmp_path, "assume", "--by", "engineer:you", "--claim", "C1", "--id", "RH",
         "--statement", "The Riemann Hypothesis", "--source", "Riemann 1859")

    # The hand edit: a third version that quietly stops listing the assumption.
    path = math_state.state_path(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    edited = dict(payload["claims"][-1])
    edited["version"] = 3
    edited["external_assumptions"] = []
    payload["claims"].append(edited)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_state(tmp_path).effective_assumptions("C1")[0].assumption_id == "RH"

    code, revised = _run(tmp_path, "revise-claim", "--id", "C1",
                         "--statement", "P, restated")
    assert code == 0
    assert [
        item["assumption_id"] for item in revised["claim"]["external_assumptions"]
    ] == ["RH"]
    _, shown = _run(tmp_path, "show", "--claim", "C1")
    assert shown["claim"]["undischarged"] == ["RH"]


# -- Lean into the kernel ---------------------------------------------------

def test_a_compiled_proof_records_mechanical_evidence_naming_kernel_and_artifact(
    tmp_path: Path,
) -> None:
    """The only path by which ``closed_kernel`` becomes reachable at all."""
    source = _proved_project(tmp_path)
    recording = record_lean_evidence(tmp_path, claim_id="C1", source=source)

    assert recording.refusals == ()
    record = recording.record
    assert record is not None
    assert record.tier is EvidenceTier.MECHANICAL
    assert record.verdict is Verdict.SUPPORTS
    assert record.produced_by == "lean_evidence/lean 4.34.0-rc1"
    assert record.artifact.startswith("research/lean/certificates/C1-")
    assert record.artifact.endswith(".json")
    archived = json.loads((tmp_path / record.artifact).read_text(encoding="utf-8"))
    assert archived["evidence_id"] == record.evidence_id
    assert archived["lean_check"] == json.loads(
        (_lean_dir(tmp_path) / "lean_check.json").read_text(encoding="utf-8")
    )
    assert archived["lean_source"]["text"] == THEOREM
    assert archived["statement_fidelity"]["text"] == FIDELITY

    _, payload = _run(tmp_path, "show", "--claim", "C1")
    assert payload["claim"]["status"] == ClaimStatus.CLOSED_KERNEL.value


def test_the_producer_names_the_proof_kernel_and_not_the_build_driver(
    tmp_path: Path,
) -> None:
    """One Lean, run bare and again under Lake, is one checker answering twice.

    ``produced_by`` is the independence key, so recording ``lake`` when a
    workspace applied would let a re-run through a different front end look
    like a second confirmation.
    """
    source = _proved_project(tmp_path)
    _result(
        tmp_path,
        tool="lake",
        tools={
            "lean": {
                "available": True,
                "path": "/x/lake env lean",
                "version": "Lean (version 4.34.0-rc1, x86_64-unknown-linux-gnu, Release)",
            },
            "lake": {
                "available": True,
                "path": "/x/lake",
                "version": "Lake version 5.0.0-src+3447a66 (Lean version 4.34.0-rc1)",
            },
        },
    )
    recording = record_lean_evidence(tmp_path, claim_id="C1", source=source)
    assert recording.record is not None
    assert recording.record.produced_by == "lean_evidence/lean 4.34.0-rc1"


def test_a_kernel_whose_version_probe_came_back_empty_is_refused(
    tmp_path: Path,
) -> None:
    """A timed-out version probe must not become a second producer.

    ``lean_check`` probes the kernel with a subprocess that has a timeout, and
    on a loaded host that probe returns ``available`` with an empty version
    while the compile itself succeeded. Naming the record ``lean_evidence/lean``
    without the version made the same kernel sort into two entries under one
    tier — fast runs versioned, slow runs not — which ``_producers_by_tier``
    groups verbatim and reads as two independent confirmations.
    """
    source = _proved_project(tmp_path)
    _result(
        tmp_path,
        tool="lake",
        tools={
            "lean": {
                "available": True,
                "path": "/x/lake env lean",
                "version": "",
            },
            "lake": {
                "available": True,
                "path": "/x/lake",
                "version": "Lake version 5.0.0-src+3447a66 (Lean version 4.34.0-rc1)",
            },
        },
    )
    recording = record_lean_evidence(tmp_path, claim_id="C1", source=source)

    assert recording.record is None
    assert any("does not name a Lean version" in item for item in recording.refusals)
    _, payload = _run(tmp_path, "show", "--claim", "C1")
    assert payload["claim"]["status"] != ClaimStatus.CLOSED_KERNEL.value


def test_a_failed_compile_is_inconclusive_and_never_refutes(
    tmp_path: Path,
) -> None:
    """``mechanical`` is a refuting tier, and Lean failing is not a disproof.

    A timeout, a missing Mathlib, or a proof the author has not finished would
    otherwise mark a true theorem false — and ``refutes`` is terminal in a way
    no amount of later evidence undoes.
    """
    source = _proved_project(tmp_path)
    _result(tmp_path, status="type_error", exit_code=1, stderr="error: unsolved goals")
    recording = record_lean_evidence(tmp_path, claim_id="C1", source=source)

    assert recording.refusals == ()
    assert recording.record is not None
    assert recording.record.tier is EvidenceTier.MECHANICAL
    assert recording.record.verdict is Verdict.INCONCLUSIVE

    _, payload = _run(tmp_path, "show", "--claim", "C1")
    assert payload["claim"]["status"] == ClaimStatus.PROPOSED.value
    assert payload["claim"]["support"] == {}


def test_an_unverified_compile_is_still_inconclusive_rather_than_unrecorded(
    tmp_path: Path,
) -> None:
    """An environment gap is a fact about the run and worth keeping.

    ``inconclusive`` is a first-class answer in this schema: "we tried and the
    host had no Mathlib" is different from silence, and the difference is what
    stops the same attempt being made every round.
    """
    source = _proved_project(tmp_path)
    _result(
        tmp_path,
        status="type_error",
        exit_code=1,
        stderr="error: unknown module prefix 'Mathlib'",
    )
    recording = record_lean_evidence(tmp_path, claim_id="C1", source=source)
    assert "lean_unverified_missing_dependency" in {
        issue.code for issue in validate_lean_evidence(tmp_path).issues
    }
    assert recording.record is not None
    assert recording.record.verdict is Verdict.INCONCLUSIVE


def test_a_forged_success_records_nothing(tmp_path: Path) -> None:
    """The cheapest attack available is a hand-written ``lean_check.json``."""
    source = _proved_project(tmp_path)
    (_lean_dir(tmp_path) / "lean_check.json").write_text(
        json.dumps({"status": "success", "source": str(source)}), encoding="utf-8"
    )
    recording = record_lean_evidence(tmp_path, claim_id="C1", source=source)
    assert recording.record is None
    assert any("not usable as evidence" in text for text in recording.refusals)
    assert load_state(tmp_path).evidence == []


def test_a_result_recorded_against_different_source_text_records_nothing(
    tmp_path: Path,
) -> None:
    """Editing the proof after it compiled must not carry the certificate."""
    source = _proved_project(tmp_path)
    source.write_text(THEOREM + "\ntheorem sneaky : False := by sorry\n", encoding="utf-8")
    recording = record_lean_evidence(tmp_path, claim_id="C1", source=source)
    assert recording.record is None
    assert load_state(tmp_path).evidence == []


def test_a_claim_that_does_not_carry_the_compiled_text_records_nothing(
    tmp_path: Path,
) -> None:
    """Lean's answer is about the file it read, and about nothing else.

    Without this, a proof of one formal statement could certify a claim
    carrying another — and because the formal statement is inside the claim's
    digest, requiring them to agree is also what makes a later retranslation
    cost the certificate.
    """
    source = _proved_project(tmp_path)
    _run(tmp_path, "claim", "--id", "C2", "--context", "ctx",
         "--statement", "something else entirely")
    recording = record_lean_evidence(tmp_path, claim_id="C2", source=source)

    assert recording.record is None
    assert any(
        "records a different formal statement" in text for text in recording.refusals
    )
    assert load_state(tmp_path).evidence == []


def test_an_unknown_claim_records_nothing(tmp_path: Path) -> None:
    """Compiling first and inventing the claim afterwards is not the order."""
    source = _proved_project(tmp_path)
    recording = record_lean_evidence(tmp_path, claim_id="ghost", source=source)
    assert recording.record is None
    assert load_state(tmp_path).evidence == []


# -- statement fidelity, which nothing in this tree verifies ----------------

def test_a_fidelity_note_edited_after_the_compile_records_nothing(
    tmp_path: Path,
) -> None:
    """The gap this PR found in the checker it was told to wire up.

    Every existing fidelity check asks whether *some* substantive note names
    the declaration, so rewriting the note after a successful compile left the
    project passing while a proof of one thing was paired with a reading
    written for another. The compile stays valid; its meaning does not.
    """
    source = _proved_project(tmp_path)
    _fidelity(tmp_path, FIDELITY + "\nActually this is about the integers.\n")

    assert "lean_fidelity_changed" in {
        issue.code for issue in validate_lean_evidence(tmp_path).issues
    }
    recording = record_lean_evidence(tmp_path, claim_id="C1", source=source)
    assert recording.record is None
    assert any("edited since this result" in text for text in recording.refusals)


def test_a_result_carrying_no_fidelity_digest_records_nothing(
    tmp_path: Path,
) -> None:
    """Fail closed on the pre-digest artifact rather than trusting it.

    ``lean_evidence check`` keeps accepting such a result, because a project
    that verified before this change is not retroactively wrong. Minting kernel
    status from one is a different question: nothing pins the reading, so the
    unchecked half of the argument is attached to nothing.
    """
    source = _proved_project(tmp_path)
    payload = json.loads((_lean_dir(tmp_path) / "lean_check.json").read_text())
    del payload["statement_fidelity_sha256"]
    (_lean_dir(tmp_path) / "lean_check.json").write_text(json.dumps(payload))

    assert validate_lean_evidence(tmp_path).issues == ()
    recording = record_lean_evidence(tmp_path, claim_id="C1", source=source)
    assert recording.record is None
    assert any("does not carry the digest" in text for text in recording.refusals)


def test_a_missing_fidelity_note_records_nothing(tmp_path: Path) -> None:
    """A compile with no statement of intent is not evidence about a claim."""
    source = _proved_project(tmp_path)
    (_lean_dir(tmp_path) / "statement_fidelity.md").unlink()
    recording = record_lean_evidence(tmp_path, claim_id="C1", source=source)
    assert recording.record is None
    assert load_state(tmp_path).evidence == []


def test_a_kernel_status_is_reported_with_the_half_nobody_checked(
    tmp_path: Path,
) -> None:
    """The schema has no field for "fidelity was verified", because nothing
    verifies it. What it must not do is let ``closed_kernel`` be read as if it
    did — so the caveat rides along with the status, and the recording names
    the document without claiming anyone checked it."""
    source = _proved_project(tmp_path)
    recording = record_lean_evidence(tmp_path, claim_id="C1", source=source)

    fidelity = recording.as_dict()["statement_fidelity"]
    assert fidelity["document"] == "research/lean/statement_fidelity.md"
    assert fidelity["verified_by"] is None
    assert "nothing has checked" in fidelity["note"]

    _, payload = _run(tmp_path, "show", "--claim", "C1")
    assert payload["claim"]["status"] == ClaimStatus.CLOSED_KERNEL.value
    assert any("nothing has checked" in text for text in payload["claim"]["caveats"])


def test_a_result_from_another_run_is_refused(tmp_path: Path) -> None:
    """The artifact lock is released before the record is written.

    Between ``verify`` returning and the record being made, another process can
    publish a different answer to the same path; pinning the expected result is
    what keeps the record about the run the caller actually performed.
    """
    source = _proved_project(tmp_path)
    stale = dict(json.loads((_lean_dir(tmp_path) / "lean_check.json").read_text()))
    stale["duration_ms"] = 999999
    recording = record_lean_evidence(
        tmp_path, claim_id="C1", source=source, expect_result=stale
    )
    assert recording.record is None
    assert any("changed between the compile" in text for text in recording.refusals)


def test_a_source_outside_the_project_records_nothing(tmp_path: Path) -> None:
    """An artifact path in project state must name something the project has."""
    _proved_project(tmp_path)
    outside = tmp_path.parent / "Elsewhere.lean"
    outside.write_text(THEOREM, encoding="utf-8")
    recording = record_lean_evidence(tmp_path, claim_id="C1", source=outside)
    assert recording.record is None
    assert any("outside the project root" in text for text in recording.refusals)


# -- the citation, after the record is written ------------------------------

ALPHA = "theorem alpha_thm : 2 + 2 = 4 := rfl\n"
ALPHA_FIDELITY = (
    "# Statement fidelity\n\n"
    "`alpha_thm` formalizes: two plus two equals four over the naturals.\n"
    "Objects: natural number literals. Quantifiers: none. Hypotheses: none.\n"
    "Conclusion: 2 + 2 reduces to 4.\n"
)
BETA = "theorem beta_thm : 1 + 1 = 2 := rfl\n"
BETA_FIDELITY = (
    "# Statement fidelity\n\n"
    "`beta_thm` formalizes: one plus one equals two over the naturals.\n"
    "Objects: natural number literals. Quantifiers: none. Hypotheses: none.\n"
    "Conclusion: 1 + 1 reduces to 2.\n"
)


def _verify_into_canonical_names(
    root: Path, claim_id: str, theorem: str, fidelity: str, statement: str
):
    """One claim taken through exactly the layout the Engineer doc teaches.

    ``research/lean/Main.lean`` and ``research/lean/statement_fidelity.md`` are
    the documented names, so the second claim reuses both — which is the whole
    point: nothing here is unusual, it is what an agent following the skill is
    told to do.
    """
    _source(root, theorem)
    _fidelity(root, fidelity)
    _result(root)
    _run(
        root, "claim", "--id", claim_id, "--context", "ctx",
        "--statement", statement,
        "--formal-file", str(_lean_dir(root) / "Main.lean"),
    )
    return record_lean_evidence(
        root, claim_id=claim_id, source=_lean_dir(root) / "Main.lean"
    )


def test_a_second_claim_does_not_overwrite_the_first_claims_certificate(
    tmp_path: Path,
) -> None:
    """Two proofs in one directory are two certificates, not one slot.

    ``verify`` publishes to fixed names, so recording the canonical
    ``lean_check.json`` made every claim in a directory cite the same file and
    the last one to compile own it. Nothing reported that: the first claim's
    status was still honestly earned, and its citation had quietly become a
    pointer to somebody else's theorem.
    """
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Arithmetic.")
    first = _verify_into_canonical_names(
        tmp_path, "A", ALPHA, ALPHA_FIDELITY, "two plus two is four"
    )
    assert first.record is not None
    second = _verify_into_canonical_names(
        tmp_path, "B", BETA, BETA_FIDELITY, "one plus one is two"
    )
    assert second.record is not None

    assert first.record.artifact != second.record.artifact
    archived = json.loads(
        (tmp_path / first.record.artifact).read_text(encoding="utf-8")
    )
    assert archived["lean_source"]["text"] == ALPHA
    assert archived["statement_fidelity"]["text"] == ALPHA_FIDELITY
    assert archived["subject"]["subject_id"] == "A"
    assert archived["lean_check"]["source_sha256"] == hashlib.sha256(
        ALPHA.encode("utf-8")
    ).hexdigest()

    # The canonical name is B's now, exactly as before; what changed is that
    # nothing cites it.
    canonical = json.loads(
        (_lean_dir(tmp_path) / "lean_check.json").read_text(encoding="utf-8")
    )
    assert canonical["source_sha256"] == hashlib.sha256(
        BETA.encode("utf-8")
    ).hexdigest()
    state = load_state(tmp_path)
    assert not any(
        item.artifact == "research/lean/lean_check.json" for item in state.evidence
    )

    # And the archives are not themselves Lean sources: `lean_evidence check`
    # discovers by extension, so a `.lean` copy would demand its own compile.
    report = validate_lean_evidence(tmp_path)
    assert [issue.as_dict() for issue in report.issues] == []
    assert len(report.sources) == 1


def test_show_names_the_certificate_so_a_reviewer_can_reach_it(
    tmp_path: Path,
) -> None:
    """Storing the certificate is half of it; being reachable is the other.

    The reviewer skill sends its reader to ``math_state show`` and then asks
    them to judge whether the formal statement says what the natural statement
    says — which cannot be done from a status, only from the compiled source
    and its fidelity note. Until this key the payload named neither, so the
    only paths a reviewer could find were the canonical ones, and in exactly
    the two-claim layout above those describe whichever claim compiled last.
    An archive nobody is told the path of leaves the reviewer reading the wrong
    theorem, which is the harm the archive was built to stop.
    """
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Arithmetic.")
    first = _verify_into_canonical_names(
        tmp_path, "A", ALPHA, ALPHA_FIDELITY, "two plus two is four"
    )
    _verify_into_canonical_names(
        tmp_path, "B", BETA, BETA_FIDELITY, "one plus one is two"
    )
    assert first.record is not None

    _, payload = _run(tmp_path, "show", "--claim", "A")

    assert payload["claim"]["certificates"] == [
        {
            "tier": EvidenceTier.MECHANICAL.value,
            "produced_by": "lean_evidence/lean 4.34.0-rc1",
            "verdict": Verdict.SUPPORTS.value,
            "artifact": first.record.artifact,
        }
    ]
    # Following it is what has to land on A's proof, not merely resolve.
    archived = json.loads(
        (tmp_path / payload["claim"]["certificates"][0]["artifact"]).read_text(
            encoding="utf-8"
        )
    )
    assert archived["lean_source"]["text"] == ALPHA


def test_show_stops_naming_a_certificate_once_the_claim_is_restated(
    tmp_path: Path,
) -> None:
    """A path beside a superseded statement is worse than no path.

    Restating a claim mints a new ``content_hash``, so the old record stops
    binding and is already reported under ``stale_evidence``. Printing its
    artifact here would send a reviewer to a certificate about a statement this
    claim no longer carries — a citation that resolves, reads as current, and
    describes something else.
    """
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Arithmetic.")
    recording = _verify_into_canonical_names(
        tmp_path, "A", ALPHA, ALPHA_FIDELITY, "two plus two is four"
    )
    assert recording.record is not None
    _, before = _run(tmp_path, "show", "--claim", "A")
    assert before["claim"]["certificates"]

    _run(tmp_path, "revise-claim", "--id", "A", "--statement", "restated")
    _, after = _run(tmp_path, "show", "--claim", "A")

    assert after["claim"]["stale_evidence"] == [recording.record.evidence_id]
    assert "certificates" not in after["claim"]


def test_recording_the_same_proof_twice_leaves_one_archive_at_one_path(
    tmp_path: Path,
) -> None:
    """Retrying is how an autonomous loop recovers, so it must not accumulate.

    The archive name is derived from the record it belongs to, not from the
    transcript, so a second run of the same proof lands on the same path rather
    than minting a per-run copy nothing points at.
    """
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Arithmetic.")
    first = _verify_into_canonical_names(
        tmp_path, "A", ALPHA, ALPHA_FIDELITY, "two plus two is four"
    )
    assert first.record is not None and first.changed

    source = _lean_dir(tmp_path) / "Main.lean"
    # A second run of the same compile: same source, same note, fresh timings.
    _result(tmp_path, duration_ms=4321)
    second = record_lean_evidence(tmp_path, claim_id="A", source=source)

    assert second.record is not None
    assert second.changed is False
    assert second.record.artifact == first.record.artifact
    assert second.record.evidence_id == first.record.evidence_id
    archives = sorted((_lean_dir(tmp_path) / "certificates").iterdir())
    assert [path.name for path in archives] == [Path(first.record.artifact).name]
    assert len(load_state(tmp_path).evidence) == 1


def _rewrite_the_note_and_reverify(root: Path, claim_id: str, note: str):
    """The exact sequence the fidelity key exists for: note edited, proof not.

    The Lean file is untouched, so the compiler answers exactly as before and
    every identifying field of the record is unchanged. Only the reading of the
    theorem the answer is paired with is different.
    """
    _fidelity(root, note)
    _result(root)
    return record_lean_evidence(
        root, claim_id=claim_id, source=_lean_dir(root) / "Main.lean"
    )


ALPHA_FIDELITY_REWRITTEN = (
    "# Statement fidelity\n\n"
    "`alpha_thm` formalizes: the numeral 4 is the sum of 2 and 2.\n"
    "Objects: natural number literals. Quantifiers: none. Hypotheses: none.\n"
    "Conclusion: the two sides are definitionally equal.\n"
)


def test_rewriting_the_fidelity_note_does_not_overwrite_the_reviewed_one(
    tmp_path: Path,
) -> None:
    """The reading a reviewer read has to survive the reading that replaced it.

    Before the note entered the certificate key, this landed on the same path:
    the record's four identifying fields were unchanged, so ``verify`` reported
    ``changed: false`` while replacing the archived document underneath it. The
    reviewer's copy of what they approved simply stopped existing.
    """
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Arithmetic.")
    first = _verify_into_canonical_names(
        tmp_path, "A", ALPHA, ALPHA_FIDELITY, "two plus two is four"
    )
    assert first.record is not None

    second = _rewrite_the_note_and_reverify(tmp_path, "A", ALPHA_FIDELITY_REWRITTEN)

    assert second.record is not None
    assert second.changed, "a different reading of the theorem is a different record"
    assert second.record.artifact != first.record.artifact
    assert second.retired == (first.record.artifact,)
    reviewed = json.loads(
        (tmp_path / first.record.artifact).read_text(encoding="utf-8")
    )
    assert reviewed["statement_fidelity"]["text"] == ALPHA_FIDELITY
    current = json.loads(
        (tmp_path / second.record.artifact).read_text(encoding="utf-8")
    )
    assert current["statement_fidelity"]["text"] == ALPHA_FIDELITY_REWRITTEN
    # One reading is in force, not two: the ledger has to be able to say which.
    assert [record.artifact for record in load_state(tmp_path).evidence] == [
        second.record.artifact
    ]


def test_a_verdict_stops_counting_when_the_reading_it_approved_is_replaced(
    tmp_path: Path,
) -> None:
    """Statement fidelity is the one verdict that must not be inherited.

    A compiler cannot check that the formal statement says what the natural
    statement says, which is why a reviewer is asked. Rewriting the note leaves
    the compile untouched — so the claim keeps ``closed_kernel`` — while making
    the reviewer's approval the only thing standing between a reading nobody
    read and a kernel status. It has to stop counting, and a status alone cannot
    say so, because the status did not move.
    """
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Arithmetic.")
    first = _verify_into_canonical_names(
        tmp_path, "A", ALPHA, ALPHA_FIDELITY, "two plus two is four"
    )
    assert first.record is not None
    _run(
        tmp_path, "judge", "--claim", "A", "--verdict", "supports",
        "--by", "reviewer/alice", "--artifact", first.record.artifact,
    )
    code, clean = _run(tmp_path, "check")
    assert (code, clean["issues"]) == (0, [])

    _rewrite_the_note_and_reverify(tmp_path, "A", ALPHA_FIDELITY_REWRITTEN)

    code, blocked = _run(tmp_path, "check")
    assert code == 1
    assert [issue["code"] for issue in blocked["issues"]] == [
        "judgement_certificate_retired"
    ]
    assert "reviewer/alice" in blocked["issues"][0]["message"]


def test_judging_the_replacement_reading_clears_the_block(tmp_path: Path) -> None:
    """A gate whose remedy does not work is worse than no gate.

    The blocked agent will do what the message says. An earlier attempt at this
    check named ``revise-claim`` as the remedy, which mints no version when the
    statement itself has not changed — so the defect could be reported and never
    cleared. The remedy here is the cheap and correct one: read the certificate
    the claim cites now, and say what you think of it.
    """
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Arithmetic.")
    first = _verify_into_canonical_names(
        tmp_path, "A", ALPHA, ALPHA_FIDELITY, "two plus two is four"
    )
    assert first.record is not None
    _run(
        tmp_path, "judge", "--claim", "A", "--verdict", "supports",
        "--by", "reviewer/alice", "--artifact", first.record.artifact,
    )
    second = _rewrite_the_note_and_reverify(tmp_path, "A", ALPHA_FIDELITY_REWRITTEN)
    assert second.record is not None

    _run(
        tmp_path, "judge", "--claim", "A", "--verdict", "supports",
        "--by", "reviewer/alice", "--artifact", second.record.artifact,
    )

    code, payload = _run(tmp_path, "check")
    assert (code, payload["issues"]) == (0, [])
    # Re-reading is one referee's one opinion, not a second producer.
    _, shown = _run(tmp_path, "show", "--claim", "A")
    assert shown["claim"]["support"]["judgement"] == ["reviewer/alice"]
    assert [
        entry["artifact"]
        for entry in shown["claim"]["certificates"]
        if entry["tier"] == EvidenceTier.JUDGEMENT.value
    ] == [second.record.artifact]


def test_a_verdict_that_cites_nothing_is_left_alone(tmp_path: Path) -> None:
    """Only a citation can go stale, so only a citation is checked.

    A judgement recorded without an artifact has said nothing about which
    document it was reached from; reporting it when the note changes would be
    inventing a claim the reviewer never made. It is also unprotected, which is
    why the reviewer skill asks for the path.
    """
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Arithmetic.")
    _verify_into_canonical_names(
        tmp_path, "A", ALPHA, ALPHA_FIDELITY, "two plus two is four"
    )
    _run(
        tmp_path, "judge", "--claim", "A", "--verdict", "supports",
        "--by", "reviewer/alice",
    )

    _rewrite_the_note_and_reverify(tmp_path, "A", ALPHA_FIDELITY_REWRITTEN)

    code, payload = _run(tmp_path, "check")
    assert (code, payload["issues"]) == (0, [])


def test_a_certificate_that_cannot_be_archived_records_nothing(
    tmp_path: Path,
) -> None:
    """Fail closed: an unarchived record is the dangling citation, silently.

    Recording anyway would leave a record whose only possible artifact is the
    canonical path the next ``verify`` rewrites — precisely the defect the
    archive exists to remove — with nothing saying the archive step was skipped.
    """
    source = _proved_project(tmp_path)
    # The directory the archive needs is occupied by a file.
    (_lean_dir(tmp_path) / "certificates").write_text("not a directory", encoding="utf-8")

    recording = record_lean_evidence(tmp_path, claim_id="C1", source=source)

    assert recording.record is None
    assert recording.changed is False
    assert any("could not be archived" in text for text in recording.refusals)
    assert any("nothing was recorded" in text for text in recording.refusals)
    assert list(load_state(tmp_path).evidence) == []
    _, payload = _run(tmp_path, "show", "--claim", "C1")
    assert payload["claim"]["status"] == ClaimStatus.PROPOSED.value


# -- what a proof costs when the theorem moves ------------------------------

def test_restating_a_claim_costs_the_proof_bound_to_the_previous_statement(
    tmp_path: Path,
) -> None:
    """Evidence goes stale by identity, and the write path must not hide it."""
    source = _proved_project(tmp_path)
    record_lean_evidence(tmp_path, claim_id="C1", source=source)
    _, before = _run(tmp_path, "show", "--claim", "C1")
    assert before["claim"]["status"] == ClaimStatus.CLOSED_KERNEL.value

    code, payload = _run(tmp_path, "revise-claim", "--id", "C1",
                         "--formal", "theorem argus_add_comm : False := by sorry")
    assert code == 0
    assert "no longer binds" in payload["note"]

    _, after = _run(tmp_path, "show", "--claim", "C1")
    assert after["claim"]["status"] == ClaimStatus.PROPOSED.value
    assert after["claim"]["stale_evidence"]


def test_an_assumption_holds_a_proved_claim_at_conditional_kernel(
    tmp_path: Path,
) -> None:
    """And no judgement discharges it, whoever writes it.

    The one place ``conditional_kernel`` and ``closed_kernel`` come apart, run
    through the commands rather than the constructors: recording a dependency
    keeps the proof (assumptions sit outside the digest) and withholds the
    status, and only a written retirement gets it back.
    """
    source = _proved_project(tmp_path)
    record_lean_evidence(tmp_path, claim_id="C1", source=source)

    _run(tmp_path, "assume", "--by", "engineer:you", "--claim", "C1", "--id", "RH",
         "--statement", "The Riemann Hypothesis", "--source", "Riemann 1859")
    _, payload = _run(tmp_path, "show", "--claim", "C1")
    assert payload["claim"]["status"] == ClaimStatus.CONDITIONAL_KERNEL.value
    assert payload["claim"]["undischarged"] == ["RH"]
    assert payload["claim"]["support"]["mechanical"]

    _run(tmp_path, "judge", "--claim", "C1", "--assumption", "RH",
         "--verdict", "supports", "--by", "reviewer:alice")
    _, judged = _run(tmp_path, "show", "--claim", "C1")
    assert judged["claim"]["status"] == ClaimStatus.CONDITIONAL_KERNEL.value

    code, retired = _run(tmp_path, "revise-claim", "--id", "C1",
                         "--retire", "RH=Lemma 2 gives the bound unconditionally")
    assert code == 0
    _, closed = _run(tmp_path, "show", "--claim", "C1")
    assert closed["claim"]["status"] == ClaimStatus.CLOSED_KERNEL.value


def test_dropping_an_assumption_without_a_reason_is_refused_from_the_cli(
    tmp_path: Path,
) -> None:
    """The store's cheapest route to ``closed_kernel``, closed at the surface too.

    ``revise-claim`` builds each version from what the claim carries rather
    than from what its last version listed, so there is no command that silently
    stops standing on something.
    """
    source = _proved_project(tmp_path)
    record_lean_evidence(tmp_path, claim_id="C1", source=source)
    _run(tmp_path, "assume", "--by", "engineer:you", "--claim", "C1", "--id", "RH",
         "--statement", "The Riemann Hypothesis", "--source", "Riemann 1859")

    code, payload = _run(tmp_path, "revise-claim", "--id", "C1",
                         "--statement", "addition is commutative, restated")
    assert code == 0
    _, after = _run(tmp_path, "show", "--claim", "C1")
    assert after["claim"]["undischarged"] == ["RH"]

    code, refused = _run(tmp_path, "revise-claim", "--id", "C1", "--retire", "RH=")
    assert code == 1
    assert "needs a reason" in refused["error"]


# -- a route that dies ------------------------------------------------------

def _open_route(root: Path) -> None:
    """A goal, a lemma it would follow from, and a live route between them."""
    _run(root, "context", "--id", "ctx", "--statement", "Fix n : Nat.")
    for claim_id, text in (("C1", "n + n is even"), ("L1", "2 divides n + n")):
        _run(root, "claim", "--id", claim_id, "--context", "ctx", "--statement", text)
    _run(root, "route", "--id", "R1", "--goal", "C1", "--obligation", "L1")


def test_a_route_is_retired_after_it_dies_and_says_what_killed_it(
    tmp_path: Path,
) -> None:
    """The order the work happens in, which the write path has to allow.

    A route is recorded before anybody starts and dies after somebody has spent
    a week on it, so a reason supplied when the route is opened could only come
    from an author who already knew the ending. Retiring the recorded route is
    the operation the Engineer doc describes, and until this verb existed the
    ledger had no way to perform it.
    """
    _open_route(tmp_path)

    code, payload = _run(tmp_path, "retire-route", "--id", "R1",
                         "--because", "the obstruction is unavoidable")
    assert code == 0
    assert payload["route"]["retired_because"] == "the obstruction is unavoidable"
    assert "takes nothing away from its goal" in payload["note"]

    # And the reason comes back out where the next planner reads it.
    _, shown = _run(tmp_path, "show", "--claim", "C1")
    route = shown["claim"]["routes"][0]
    assert route["route_id"] == "R1"
    assert route["status"] == RouteStatus.RETIRED.value
    assert route["retired_because"] == "the obstruction is unavoidable"
    assert _run(tmp_path, "check")[0] == 0


def test_a_route_cannot_be_retired_without_saying_why(tmp_path: Path) -> None:
    """Whitespace as well as nothing: the field exists to be said, not filled.

    Abandoning a decomposition asserts that the mathematics does not go through
    that way, which is a claim about the problem. A blank reason retires the
    route and teaches the next planner nothing, which is the state the schema
    carries a reason rather than a flag to prevent.
    """
    _open_route(tmp_path)
    before = math_state.state_path(tmp_path).read_bytes()

    for blank in ("", "   \n\t "):
        code, payload = _run(tmp_path, "retire-route", "--id", "R1", "--because", blank)
        assert code == 1
        assert "needs a reason" in payload["error"]

    assert math_state.state_path(tmp_path).read_bytes() == before
    _, shown = _run(tmp_path, "show", "--claim", "C1")
    assert shown["claim"]["routes"][0]["status"] == RouteStatus.OPEN.value


def test_retiring_a_route_nobody_recorded_is_refused(tmp_path: Path) -> None:
    """A retirement is a fact about a plan, so there has to be a plan.

    Writing one anyway would mint a route out of a typo, and a route recorded
    dead by accident is one nobody will ever attempt.
    """
    _open_route(tmp_path)

    code, payload = _run(tmp_path, "retire-route", "--id", "R2",
                         "--because", "the obstruction is unavoidable")
    assert code == 1
    assert "no route 'R2'" in payload["error"]
    assert "record the decomposition" in payload["error"]
    assert [route.route_id for route in load_state(tmp_path).routes] == ["R1"]


def test_a_retired_route_keeps_the_reason_it_was_retired_for(tmp_path: Path) -> None:
    """Unset to set, and no further: the first reason is the record.

    A second reason in the same place leaves a reader unable to say which
    obstruction the project actually met, and the recorded one is what they have
    instead of the attempt itself. Repeating the same retirement is not a
    rewrite and stays quiet, because retrying is how an autonomous loop
    recovers.
    """
    _open_route(tmp_path)
    _run(tmp_path, "retire-route", "--id", "R1",
         "--because", "the obstruction is unavoidable")

    code, refused = _run(tmp_path, "retire-route", "--id", "R1",
                         "--because", "we ran out of time")
    assert code == 1
    assert "already retired" in refused["error"]
    assert "the obstruction is unavoidable" in refused["error"]
    assert "own id" in refused["error"]

    code, repeated = _run(tmp_path, "retire-route", "--id", "R1",
                          "--because", "the obstruction is unavoidable")
    assert code == 0
    assert repeated["unchanged"] is True

    _, shown = _run(tmp_path, "show", "--claim", "C1")
    assert shown["claim"]["routes"][0]["retired_because"] == (
        "the obstruction is unavoidable"
    )


def test_retiring_a_route_leaves_its_goal_exactly_where_the_evidence_put_it(
    tmp_path: Path,
) -> None:
    """A route confers nothing, so burying one can take nothing away.

    The failure this guards is the mirror of the one ``with_routes`` guards: if
    a discharged route must not promote its goal, a dead route must not demote
    it. The claim's assessment is compared whole, so a later edit that let a
    retirement touch support, undischarged assumptions, or stale evidence fails
    here rather than in a run.
    """
    _open_route(tmp_path)
    _run(tmp_path, "judge", "--claim", "C1", "--verdict", "supports",
         "--by", "reviewer:alice")

    _, before = _run(tmp_path, "show", "--claim", "C1")
    assert before["claim"]["status"] == ClaimStatus.SUPPORTED.value

    assert _run(tmp_path, "retire-route", "--id", "R1",
                "--because", "the obstruction is unavoidable")[0] == 0

    _, after = _run(tmp_path, "show", "--claim", "C1")
    assert after["claim"]["status"] == ClaimStatus.SUPPORTED.value
    assert {key: value for key, value in after["claim"].items() if key != "routes"} == {
        key: value for key, value in before["claim"].items() if key != "routes"
    }
    # The lemma the dead route asked for is still a claim in its own right.
    _, lemma = _run(tmp_path, "show", "--claim", "L1")
    assert lemma["claim"]["status"] == ClaimStatus.PROPOSED.value


def test_only_the_retirement_command_rewrites_a_recorded_route() -> None:
    """Monotonic retirement is a property of one function or of none.

    The tests above would all still pass if a later helper assigned
    ``state.routes`` from somewhere that skipped the check, which is how the
    reason a route died becomes something that can be quietly replaced. This
    reads the source instead, and fails the moment a second door exists.
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    writers: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign | ast.AugAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and target.value.attr == "routes"
                ):
                    writers.add(function.name)
    assert writers == {"_cmd_retire_route"}

    openers = {
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_route"
    }
    assert openers == {"_cmd_route"}


# -- an unreferenced CLI is the same as no CLI ------------------------------

SKILLS = REPO_ROOT / "argus" / "verticals" / "math" / "skills"


def test_the_engineer_is_told_which_commands_write_the_ledger() -> None:
    """A write path nobody is told to use records nothing on any real run.

    That is the failure this whole PR exists to fix, one layer up: the kernel
    was complete and unreachable because no code called it. A CLI that no skill
    mentions is unreachable for the same reason, by an agent instead of by a
    function.
    """
    text = (SKILLS / "engineer" / "math-research-execution.md").read_text(
        encoding="utf-8"
    )
    parser = math_state._build_parser()
    group = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    missing = [name for name in group.choices if f"$S {name} " not in text]
    assert missing == [], f"commands the Engineer is never told about: {missing}"
    assert "verify Main.lean" in text
    assert "--claim C1" in text


def test_the_reviewer_is_told_what_a_kernel_status_does_not_include() -> None:
    """The caveat is only useful if it reaches the role that can act on it."""
    text = (SKILLS / "reviewer" / "math-research-review.md").read_text(
        encoding="utf-8"
    )
    assert "math_state show" in text
    assert "math_state judge" in text
    assert "inconclusive" in text


def test_no_skill_tells_an_agent_to_select_an_evidence_tier() -> None:
    """Prose is a surface too: an instruction to pass a flag that does not exist
    teaches an agent to look for one, and the answer must be that there is none."""
    offenders: list[str] = []
    for path in sorted(SKILLS.rglob("*.md")):
        text = path.read_text(encoding="utf-8").lower()
        for phrase in ("--tier", "--force", "tier mechanical", "tier=mechanical"):
            if phrase in text:
                offenders.append(f"{path.name}: {phrase}")
    assert offenders == []


# -- end to end, on a host that has Lean ------------------------------------

@requires_lean
def test_a_real_compile_reaches_closed_kernel_through_the_documented_commands(
    tmp_path: Path,
) -> None:
    """No synthesized artifact anywhere: the compiler decides the status.

    This is the claim the PR rests on — that ``closed_kernel`` is reachable
    only by running a proof kernel — and the only way to check it is to run one.
    """
    from argus.verticals.math.lean_evidence import main as lean_main

    source = _source(tmp_path)
    fidelity = _fidelity(tmp_path)
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Natural numbers.")
    _run(tmp_path, "claim", "--id", "C1", "--context", "ctx",
         "--statement", "addition of naturals is commutative",
         "--formal-file", str(source))

    code = lean_main([
        "verify", str(source),
        "--statement-fidelity", str(fidelity),
        "--claim", "C1",
        "--project-root", str(tmp_path),
        "--timeout", "300",
    ])
    assert code == 0

    _, payload = _run(tmp_path, "show", "--claim", "C1")
    assert payload["claim"]["status"] == ClaimStatus.CLOSED_KERNEL.value
    producers = payload["claim"]["support"]["mechanical"]
    assert len(producers) == 1
    assert producers[0].startswith("lean_evidence/lean ")


@requires_lean
def test_a_real_compile_failure_leaves_the_claim_unproved_and_says_so(
    tmp_path: Path,
) -> None:
    """The other half: the exit code and the state agree that nothing was proved."""
    from argus.verticals.math.lean_evidence import main as lean_main

    source = _source(
        tmp_path, "theorem argus_false (n : Nat) : n = n + 1 := by rfl\n"
    )
    fidelity = _fidelity(
        tmp_path,
        "# Statement fidelity\n\n`argus_false` states that every natural number "
        "equals its successor. Objects: naturals. Hypotheses: none. This is "
        "false, and the formalization renders the false claim faithfully.\n",
    )
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Natural numbers.")
    _run(tmp_path, "claim", "--id", "C1", "--context", "ctx",
         "--statement", "every natural equals its successor",
         "--formal-file", str(source))

    assert lean_main([
        "verify", str(source),
        "--statement-fidelity", str(fidelity),
        "--claim", "C1",
        "--project-root", str(tmp_path),
        "--timeout", "300",
    ]) == 1

    _, payload = _run(tmp_path, "show", "--claim", "C1")
    assert payload["claim"]["status"] == ClaimStatus.PROPOSED.value
    assert payload["claim"]["support"] == {}


@requires_lean
def test_two_real_compiles_in_the_documented_directory_keep_both_certificates(
    tmp_path: Path,
) -> None:
    """The reported scenario, end to end, with nothing synthesized.

    Both claims are formalized exactly as the Engineer skill teaches — one
    ``research/lean/Main.lean``, one ``research/lean/statement_fidelity.md``,
    rewritten for the second theorem — and both compile for real. Before the
    archive, step five held: ``lean_check.json`` described only ``beta_thm``,
    both records named it, and neither ``math_state show`` nor ``lean_evidence
    check`` said a word about it.
    """
    from argus.verticals.math.lean_evidence import main as lean_main

    def verify(claim_id: str, theorem: str, note: str, statement: str) -> None:
        source = _source(tmp_path, theorem)
        fidelity = _fidelity(tmp_path, note)
        _run(tmp_path, "claim", "--id", claim_id, "--context", "ctx",
             "--statement", statement, "--formal-file", str(source))
        assert lean_main([
            "verify", str(source),
            "--statement-fidelity", str(fidelity),
            "--claim", claim_id,
            "--project-root", str(tmp_path),
            "--timeout", "300",
        ]) == 0

    _run(tmp_path, "context", "--id", "ctx", "--statement", "Arithmetic.")
    verify("A", ALPHA, ALPHA_FIDELITY, "two plus two is four")
    verify("B", BETA, BETA_FIDELITY, "one plus one is two")

    _, shown = _run(tmp_path, "show", "--claim", "A")
    assert shown["claim"]["status"] == ClaimStatus.CLOSED_KERNEL.value
    assert shown["claim"]["stale_evidence"] == []
    assert shown["claim"]["issues"] == []
    assert lean_main(["check", "--project-root", str(tmp_path)]) == 0

    records = {item.subject.subject_id: item for item in load_state(tmp_path).evidence}
    assert records["A"].artifact != records["B"].artifact
    cited = json.loads(
        (tmp_path / records["A"].artifact).read_text(encoding="utf-8")
    )
    assert cited["lean_source"]["text"] == ALPHA
    assert cited["lean_check"]["status"] == "success"
    assert "alpha_thm" in cited["statement_fidelity"]["text"]
    # The canonical file is B's, which is exactly why A must not cite it.
    assert "beta_thm" in (_lean_dir(tmp_path) / "Main.lean").read_text(
        encoding="utf-8"
    )


def test_retire_revise_and_replace_round_trip_keeps_check_green(tmp_path: Path) -> None:
    """The sequence from lbx154/Argus#127, through the public commands.

    Retiring a route, revising the claim it aimed at, and opening a replacement
    against the revised statement is legitimate history. Both statements, the
    original route with its reason, and the replacement all stay in the file.
    """
    _run(tmp_path, "context", "--id", "ctx", "--statement", "Integers with usual order.")
    _run(tmp_path, "claim", "--id", "C1", "--context", "ctx", "--statement", "2 > 0.")
    _run(tmp_path, "claim", "--id", "L1", "--context", "ctx", "--statement", "2 > 1.")
    assert _run(tmp_path, "route", "--id", "R1", "--goal", "C1", "--obligation", "L1")[0] == 0
    assert _run(tmp_path, "check")[0] == 0
    assert _run(tmp_path, "retire-route", "--id", "R1", "--because", "Replaced by a stronger claim.")[0] == 0
    assert _run(tmp_path, "revise-claim", "--id", "C1", "--statement", "2 > 1 > 0.")[0] == 0
    assert _run(tmp_path, "route", "--id", "R2", "--goal", "C1", "--obligation", "L1")[0] == 0

    code, payload = _run(tmp_path, "check")
    assert code == 0, payload

    state = load_state(tmp_path)
    assert sorted(item.natural_statement for item in state.claims if item.claim_id == "C1") == ["2 > 0.", "2 > 1 > 0."]
    routes = {item.route_id: item for item in state.routes}
    assert routes["R1"].retired_because == "Replaced by a stronger claim."
    assert routes["R1"].goal.content_hash != routes["R2"].goal.content_hash
    assert routes["R2"].retired_because == ""
