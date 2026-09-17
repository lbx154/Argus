"""The citation checker: what it can settle, what it cannot, and when it blocks.

Two layers with opposite failure modes, so they are tested for opposite things.
Existence is mechanical and its danger is over-claiming — a registry that
answers "yes" has said nothing about the proposition, and a registry that does
not answer has said nothing at all, so the tests below are mostly about the
answers this layer must *not* give. Attribution ends in a reader's word, and its
danger is the unexaminable verdict, so those tests are about the passage the
program insists on before it will record one.

The third thing covered here is when any of it blocks. The requirement is that
checking never interrupts the mathematics and always precedes delivery, which is
one assertion about ``solve`` and one about ``review``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from argus.proof_ledger import (
    CitationStatus,
    ClaimStatus,
    EvidenceTier,
    Verdict,
    load_state,
)
from argus.verticals.math import citation_check
from argus.verticals.math.citation_check import (
    DELIVERABLE_STATUSES,
    attribute_citation,
    resolve_citation,
    resolve_source,
)
from argus.verticals.math.math_state import (
    LITERATURE_RELPATH,
)
from argus.verticals.math.math_state import (
    main as math_state_main,
)
from argus.verticals.math.objective_mode import set_objective
from argus.verticals.math.stages import stage_completion_issues

DOI = "doi:10.1093/oso/9780198533696.001.0001"
LOCATOR = "Theorem 14.2"
EXCERPT = (
    "Theorem 14.2. Assume the Riemann hypothesis. Then for every eps > 0 the "
    "error term in the prime number theorem is O(x^(1/2+eps)).\n"
)


# -- fixtures ----------------------------------------------------------------

def _state(root: Path, *argv: str) -> dict:
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = math_state_main([argv[0], "--project-root", str(root), *argv[1:]])
    payload = json.loads(buffer.getvalue())
    assert code == 0, payload
    return payload


def _project(root: Path, *, source_id: str = DOI, locator: str = LOCATOR) -> None:
    """One claim standing on one imported result, which is the whole subject.

    The objective is set so the completion gate gets past its own preconditions
    and reaches the citations; ``exploratory`` because a proof graph is a
    different test's subject.
    """
    set_objective(root, mode="exploratory", goal="")
    _state(root, "context", "--id", "ctx", "--statement", "Zeta zeros and primes.")
    _state(
        root,
        "claim",
        "--id",
        "C1",
        "--context",
        "ctx",
        "--statement",
        "The prime counting error term is small.",
    )
    argv = [
        "assume",
        "--by",
        "engineer:you",
        "--claim",
        "C1",
        "--id",
        "rh",
        "--statement",
        "Every nontrivial zero has real part one half.",
        "--source",
        "Titchmarsh, The Theory of the Riemann Zeta-Function",
    ]
    if source_id:
        argv += ["--source-id", source_id]
    if locator:
        argv += ["--locator", locator]
    _state(root, *argv)


def _status(root: Path) -> CitationStatus:
    state = load_state(root)
    (citation,) = state.citations("C1")
    return citation.status


def _feed(present: bool) -> str:
    entry = "<entry><title>The Theory of the Riemann Zeta-Function</title></entry>"
    return f"<feed>{entry if present else ''}</feed>"


def _handle(code: int) -> str:
    return json.dumps({"responseCode": code, "handle": "10.1093/oso"})


# -- layer one: existence ----------------------------------------------------

def test_a_document_that_resolves_confirms_nothing(tmp_path: Path) -> None:
    """The lookup asked about a paper; the citation is about a theorem in it.

    This is the whole reason the existence layer cannot be the gate. Recording
    ``supports`` here would let a real paper clear a check that is asking
    whether the paper contains Theorem 14.2, which is the failure mode citation
    checking exists for — the reference is genuine and the proposition is not
    in it.
    """
    _project(tmp_path)
    payload = resolve_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        fetch=lambda url: (200, _handle(1)),
    )
    assert payload["ok"] is True
    assert payload["resolution"]["outcome"] == "present"
    assert payload["recorded"]["verdict"] == Verdict.INCONCLUSIVE.value
    assert _status(tmp_path) is CitationStatus.INCONCLUSIVE
    assert _status(tmp_path) not in DELIVERABLE_STATUSES


def test_an_identifier_no_registry_knows_is_refuted(tmp_path: Path) -> None:
    """The fabricated DOI, caught by a program rather than by a reader."""
    _project(tmp_path)
    payload = resolve_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        fetch=lambda url: (404, _handle(100)),
    )
    assert payload["resolution"]["outcome"] == "absent"
    assert payload["recorded"]["verdict"] == Verdict.REFUTES.value
    assert _status(tmp_path) is CitationStatus.DISPUTED


def test_a_registry_that_did_not_answer_has_not_found_anything(
    tmp_path: Path,
) -> None:
    """An offline host must not report every citation in the project as false.

    ``refutes`` is in ``REFUTING_TIERS`` for this tier, so the difference
    between "the handle service says no" and "nothing reached the handle
    service" is the difference between a finding and a firewall.
    """
    _project(tmp_path)

    def unreachable(url: str) -> tuple[int, str]:
        raise OSError("Network is unreachable")

    payload = resolve_citation(
        tmp_path, claim_id="C1", assumption_id="rh", fetch=unreachable
    )
    assert payload["resolution"]["outcome"] == "unreachable"
    assert payload["recorded"]["verdict"] == Verdict.INCONCLUSIVE.value
    assert "Network is unreachable" in payload["resolution"]["detail"]
    assert _status(tmp_path) is CitationStatus.INCONCLUSIVE


def test_a_service_that_declines_to_answer_is_not_a_denial(tmp_path: Path) -> None:
    """``responseCode`` 2 is the handle service erroring, not answering no."""
    _project(tmp_path)
    payload = resolve_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        fetch=lambda url: (500, _handle(2)),
    )
    assert payload["resolution"]["outcome"] == "unreachable"
    assert payload["recorded"]["verdict"] == Verdict.INCONCLUSIVE.value


def test_a_scheme_this_checker_cannot_interrogate_says_so(tmp_path: Path) -> None:
    """An ISBN is a legitimate source, not a missing one.

    Reporting ``absent`` for every identifier without a registry here would make
    the layer's refutations worthless: they would mean "unrecognized" far more
    often than "fabricated".
    """
    resolution = resolve_source("isbn:9780198533696")
    assert resolution.outcome == "unsupported"
    assert resolution.verdict is Verdict.INCONCLUSIVE
    assert resolution.endpoint == ""


@pytest.mark.parametrize(
    ("status", "body"),
    [(429, "Too Many Requests"), (403, "Forbidden"), (503, "Service Unavailable")],
)
def test_an_arxiv_http_failure_cannot_refute_a_citation(
    tmp_path: Path, status: int, body: str,
) -> None:
    _project(tmp_path, source_id="arxiv:2504.01234v2")
    payload = resolve_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        fetch=lambda url: (status, body),
    )
    assert payload["resolution"]["outcome"] == "unreachable"
    assert f"HTTP {status}" in payload["resolution"]["detail"]
    assert payload["recorded"]["verdict"] == Verdict.INCONCLUSIVE.value
    assert _status(tmp_path) is CitationStatus.INCONCLUSIVE
    assert _status(tmp_path) not in DELIVERABLE_STATUSES


def test_arxiv_answers_two_hundred_for_a_paper_that_does_not_exist(
    tmp_path: Path,
) -> None:
    """So the status line cannot be what this layer reads."""
    absent = resolve_source("arxiv:2504.99999v1", fetch=lambda url: (200, _feed(False)))
    assert absent.outcome == "absent"
    assert absent.verdict is Verdict.REFUTES

    present = resolve_source("arxiv:2504.01234v2", fetch=lambda url: (200, _feed(True)))
    assert present.outcome == "present"
    assert present.verdict is Verdict.INCONCLUSIVE
    assert "2504.01234v2" in present.endpoint


# -- layer two: attribution --------------------------------------------------

def test_a_reader_confirms_a_citation_against_a_passage_on_disk(
    tmp_path: Path,
) -> None:
    """The verdict is a person's word; the passage is what makes it examinable."""
    _project(tmp_path)
    payload = attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt=EXCERPT,
        verdict=Verdict.SUPPORTS,
        checked_by="reader:bob",
    )
    assert payload["ok"] is True
    assert payload["status"] == CitationStatus.CONFIRMED.value

    archive = tmp_path / payload["archive"]
    assert archive.parent == tmp_path.joinpath(*LITERATURE_RELPATH)
    envelope = json.loads(archive.read_text(encoding="utf-8"))
    assert envelope["kind"] == "excerpt"
    assert envelope["text"] == EXCERPT.strip()
    assert (envelope["source_id"], envelope["locator"]) == (DOI, LOCATOR)

    (record,) = load_state(tmp_path).evidence
    assert record.tier is EvidenceTier.LITERATURE
    assert record.artifact == payload["archive"]


def test_the_archived_passage_names_the_proposition_it_was_filed_against(
    tmp_path: Path,
) -> None:
    """Stamped in from the assumption, so a caller cannot make them disagree.

    Passed in, the two could drift: an excerpt archived as Theorem 3.1 while the
    record binds to the assumption citing Theorem 3.2 would be a confirmation
    pointing at the wrong proposition, and nothing downstream would notice.
    """
    _project(tmp_path)
    recorded = citation_check.record_citation_evidence(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        verdict=Verdict.SUPPORTS,
        produced_by="reader:bob",
        retrieval={
            "kind": "excerpt",
            "text": EXCERPT,
            "source_id": "doi:10.0000/fabricated",
            "locator": "Theorem 1.1",
        },
    )
    envelope = json.loads(
        (tmp_path / recorded.archive).read_text(encoding="utf-8")
    )
    assert (envelope["source_id"], envelope["locator"]) == (DOI, LOCATOR)


def test_an_assertion_too_short_to_be_a_quotation_records_nothing(
    tmp_path: Path,
) -> None:
    """"It checks out" is an opinion, and the tier for opinions is judgement."""
    _project(tmp_path)
    payload = attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt="it checks out\n",
        verdict=Verdict.SUPPORTS,
        checked_by="reader:bob",
    )
    assert payload["ok"] is False
    assert "does not fit" in payload["refusals"][0]
    assert load_state(tmp_path).evidence == []
    assert not tmp_path.joinpath(*LITERATURE_RELPATH).exists()


def test_a_reader_cannot_file_their_word_as_a_registry_answer(
    tmp_path: Path,
) -> None:
    """``produced_by`` is the independence key, so impersonating a lookup would
    make one reader look like a reader and a registry agreeing."""
    _project(tmp_path)
    payload = attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt=EXCERPT,
        verdict=Verdict.SUPPORTS,
        checked_by="citation_check/doi.org",
    )
    assert payload["ok"] is False
    assert "reserved" in payload["refusals"][0]
    assert load_state(tmp_path).evidence == []


def test_an_assumption_citing_prose_has_nothing_to_check(tmp_path: Path) -> None:
    """``uncited`` is a state, not a queue entry, so a check of one is refused.

    The alternative is a work list containing tasks nobody can close, which is
    how a gate stops being read.
    """
    _project(tmp_path, source_id="", locator="")
    assert _status(tmp_path) is CitationStatus.UNCITED
    payload = attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt=EXCERPT,
        verdict=Verdict.SUPPORTS,
        checked_by="reader:bob",
    )
    assert payload["ok"] is False
    assert "names no proposition" in payload["refusals"][0]


def test_half_a_citation_is_told_which_half_is_missing(tmp_path: Path) -> None:
    """A document with no locator is not prose, and saying so would misdirect.

    ``check`` already refuses this shape as ``citation_incomplete``; the refusal
    here has to name the same defect, or a checker sent to close the queue is
    told to add a source it already has.
    """
    _project(tmp_path, locator="")
    payload = attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt=EXCERPT,
        verdict=Verdict.SUPPORTS,
        checked_by="reader:bob",
    )
    assert payload["ok"] is False
    assert "half a citation" in payload["refusals"][0]
    assert load_state(tmp_path).evidence == []


def test_two_workers_retrieving_the_same_passage_write_one_file(
    tmp_path: Path,
) -> None:
    """The archive path is a function of its content, which is why no lock.

    Concurrency here is not serialized, it is made unnecessary: two writers can
    only collide on a path when they are writing the same bytes, so the loser of
    the race loses nothing. The second recording changes the state not at all.
    """
    _project(tmp_path)
    first = attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt=EXCERPT,
        verdict=Verdict.SUPPORTS,
        checked_by="reader:bob",
    )
    second = attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt=EXCERPT,
        verdict=Verdict.SUPPORTS,
        checked_by="reader:bob",
    )
    assert first["archive"] == second["archive"]
    assert first["changed"] is True
    assert second["changed"] is False
    assert len(list(tmp_path.joinpath(*LITERATURE_RELPATH).iterdir())) == 1
    assert len(load_state(tmp_path).evidence) == 1


def test_one_reader_who_goes_back_replaces_their_own_answer(
    tmp_path: Path,
) -> None:
    """Otherwise a reader correcting themselves reads as two disagreeing ones.

    ``assess_citation`` prefers a refutation over a confirmation, so a stale
    ``supports`` left beside a corrected ``refutes`` would not change the
    reported status — it would leave the ledger unable to say the confirmation
    had been withdrawn, and would count one reader as two.
    """
    _project(tmp_path)
    attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt=EXCERPT,
        verdict=Verdict.SUPPORTS,
        checked_by="reader:bob",
    )
    attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt="Theorem 14.2 is about the divisor problem, not the error term.\n",
        verdict=Verdict.REFUTES,
        checked_by="reader:bob",
    )
    evidence = load_state(tmp_path).evidence
    assert [record.verdict for record in evidence] == [Verdict.REFUTES]
    assert _status(tmp_path) is CitationStatus.DISPUTED


def test_correcting_the_theorem_number_drops_the_confirmation(
    tmp_path: Path,
) -> None:
    """A check of Theorem 14.2 does not quietly become a check of Theorem 14.3."""
    _project(tmp_path)
    attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt=EXCERPT,
        verdict=Verdict.SUPPORTS,
        checked_by="reader:bob",
    )
    assert _status(tmp_path) is CitationStatus.CONFIRMED
    _state(
        tmp_path,
        "assume",
        "--by",
        "engineer:you",
        "--claim",
        "C1",
        "--id",
        "rh",
        "--statement",
        "Every nontrivial zero has real part one half.",
        "--source",
        "Titchmarsh, The Theory of the Riemann Zeta-Function",
        "--source-id",
        DOI,
        "--locator",
        "Theorem 14.3",
    )
    assert _status(tmp_path) is CitationStatus.UNCHECKED


def test_a_confirmed_citation_still_discharges_nothing(tmp_path: Path) -> None:
    """The source contains the proposition. Whether its hypotheses hold here is
    a third question, and no tier in this schema answers it by retrieval."""
    _project(tmp_path)
    attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt=EXCERPT,
        verdict=Verdict.SUPPORTS,
        checked_by="reader:bob",
    )
    state = load_state(tmp_path)
    assert state.assess("C1").status is ClaimStatus.PROPOSED
    assert state.undischarged_assumptions("C1")


# -- when it blocks ----------------------------------------------------------

def _issues(root: Path, stage: str) -> tuple[str, ...]:
    """The gate's complaints about citations, with the rest of it filtered out."""
    return tuple(
        issue
        for issue in stage_completion_issues(stage, root)
        if "stands on" in issue
    )


def test_an_unchecked_citation_does_not_stop_the_mathematics(
    tmp_path: Path,
) -> None:
    """Checking is out of band, so ``solve`` completes with it outstanding.

    A gate here would make the asynchronous design pointless: the reasoning
    would stop to wait for a registry, and the first thing anyone would do is
    stop running the checker.
    """
    _project(tmp_path)
    assert _status(tmp_path) is CitationStatus.UNCHECKED
    assert _issues(tmp_path, "solve") == ()


def test_nothing_ships_standing_on_a_citation_nobody_looked_up(
    tmp_path: Path,
) -> None:
    _project(tmp_path)
    (issue,) = _issues(tmp_path, "review")
    assert "rh" in issue and "unchecked" in issue
    # The remedy has to work on the machine that is blocked, and this one is
    # offline by construction: a reader who has the paper needs no registry.
    assert "citation_check attribute" in issue
    assert "restate the assumption" in issue

    attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt=EXCERPT,
        verdict=Verdict.SUPPORTS,
        checked_by="reader:bob",
    )
    assert _issues(tmp_path, "review") == ()


def test_a_citation_somebody_checked_and_could_not_find_still_blocks(
    tmp_path: Path,
) -> None:
    """Settled is a question about the queue; this is a question about the result.

    ``disputed`` owes nobody another lookup, so it is absent from the work list
    — and shipping a proof that leans on a proposition a reader went and failed
    to find is exactly what this gate is for.
    """
    _project(tmp_path)
    resolve_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        fetch=lambda url: (404, _handle(100)),
    )
    assert _status(tmp_path) is CitationStatus.DISPUTED
    assert load_state(tmp_path).open_citations() == {}
    (issue,) = _issues(tmp_path, "review")
    assert "disputed" in issue


def test_a_source_that_cites_prose_ships(tmp_path: Path) -> None:
    """An unciteable dependency stated as one is a reader's problem to weigh,
    not a lookup somebody failed to perform."""
    _project(tmp_path, source_id="", locator="")
    assert _issues(tmp_path, "review") == ()


def test_a_project_with_no_ledger_pays_nothing(tmp_path: Path) -> None:
    set_objective(tmp_path, mode="exploratory", goal="")
    assert _issues(tmp_path, "review") == ()


# -- the surface -------------------------------------------------------------

def test_the_checker_offers_no_option_that_selects_a_tier() -> None:
    """The same sweep the state CLI gets, because this parser reaches further.

    ``math_state``'s surface cannot write ``literature`` at all; this one can,
    and the thing that makes that legitimate is the archived passage rather than
    a promise. A flag that recorded a verdict without one — ``--assume-checked``,
    ``--tier``, ``--no-excerpt`` — would take the property away, so the sweep
    reads the parser rather than trusting today's flags.
    """
    forbidden = (
        "tier", "force", "unsafe", "override", "mechanical", "computational",
        "literature", "kernel", "trust", "skip", "no-verify", "admin", "assume",
    )
    parser = citation_check._build_parser()
    groups = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert groups, "the sweep found no subcommands, so it proves nothing"

    offenders = [
        f"{name} {option}"
        for group in groups
        for name, child in group.choices.items()
        for action in child._actions
        for option in action.option_strings
        if any(word in option.lower() for word in forbidden)
    ]
    assert offenders == []

    attribute = groups[0].choices["attribute"]
    excerpt = next(
        action for action in attribute._actions if action.dest == "excerpt_file"
    )
    assert excerpt.required is True


def test_the_command_line_reports_what_is_outstanding_and_exits_on_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The work list an out-of-band checker reads, derived rather than stored."""
    _project(tmp_path)
    code = citation_check.main(["status", "--project-root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["blocking"]["C1"][0]["assumption_id"] == "rh"
    assert payload["claims"]["C1"][0]["cited_proposition"] == f"{DOI} {LOCATOR}"

    attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt=EXCERPT,
        verdict=Verdict.SUPPORTS,
        checked_by="reader:bob",
    )
    code = citation_check.main(["status", "--project-root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["blocking"] == {}


# ---------------------------------------------------------------------------
# The reader whose reading is under review
# ---------------------------------------------------------------------------
#
# The Reviewer skill has always said this in prose: "the worker who wrote
# 'Theorem 3.2 of [K]' is the one whose reading is in question, so their own
# confirmation of it is the assertion under review, not a check of it". Nothing
# held it. An engineer could file the assumption, then file a supporting
# literature verdict under any string at all -- including its own name -- and
# the citation read `confirmed`, cleared `review`, and shipped.


def test_the_filer_cannot_confirm_their_own_citation(tmp_path: Path) -> None:
    _project(tmp_path)
    excerpt = tmp_path / "read.txt"
    excerpt.write_text(EXCERPT, encoding="utf-8")

    result = attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt=EXCERPT,
        verdict=Verdict.SUPPORTS,
        checked_by="engineer:you",
    )

    assert result["ok"] is False
    assert result["recorded"] is None
    (refusal,) = result["refusals"]
    assert "filed this assumption" in refusal
    assert _status(tmp_path) is CitationStatus.UNCHECKED


def test_the_filer_can_still_refute_their_own_citation(tmp_path: Path) -> None:
    """A report against interest is the one thing self-checking cannot fake.

    Discarding it would be strictly worse than the hole it closes: a worker who
    went back, found the theorem was not there, and said so would be unable to
    record it, and the wrong citation would survive on the strength of nobody
    having filed anything.
    """
    _project(tmp_path)

    result = attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt="Theorem 14.2 is about the divisor function and says nothing "
        "whatever about zeta zeros or the error term.\n",
        verdict=Verdict.REFUTES,
        checked_by="engineer:you",
    )

    assert result["ok"] is True
    assert _status(tmp_path) is CitationStatus.DISPUTED


def test_a_self_supported_citation_is_reported_not_silently_confirmed(
    tmp_path: Path,
) -> None:
    """The write-time refusal is the error message; this is the gate.

    A record can reach the ledger without passing through ``attribute`` -- a
    hand edit, a direct kernel call, an older file. The status is derived, so
    it does not matter how the record got there.
    """
    from argus.verticals.math.math_state import record_citation_evidence

    _project(tmp_path)
    record_citation_evidence(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        verdict=Verdict.SUPPORTS,
        produced_by="engineer:you",
        retrieval={"kind": "excerpt", "text": EXCERPT},
    )

    assert _status(tmp_path) is CitationStatus.SELF_CHECKED


def test_a_self_checked_citation_does_not_ship(tmp_path: Path) -> None:
    _project(tmp_path)
    from argus.verticals.math.math_state import record_citation_evidence

    record_citation_evidence(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        verdict=Verdict.SUPPORTS,
        produced_by="engineer:you",
        retrieval={"kind": "excerpt", "text": EXCERPT},
    )

    assert CitationStatus.SELF_CHECKED not in DELIVERABLE_STATUSES
    issues = stage_completion_issues("review", tmp_path)
    assert any("rh" in issue for issue in issues), issues


def test_one_independent_reader_is_enough(tmp_path: Path) -> None:
    _project(tmp_path)

    assert (
        attribute_citation(
            tmp_path,
            claim_id="C1",
            assumption_id="rh",
            excerpt=EXCERPT,
            verdict=Verdict.SUPPORTS,
            checked_by="reviewer:alice",
        )["ok"]
        is True
    )

    assert _status(tmp_path) is CitationStatus.CONFIRMED
    assert not [
        issue for issue in stage_completion_issues("review", tmp_path) if "rh" in issue
    ]


def test_re_filing_does_not_reassign_the_filer(tmp_path: Path) -> None:
    """Otherwise the rule costs one command to get around.

    The filer is outside the assumption's digest -- deliberately, so that a
    refutation cannot be shed by restating the assumption -- which means a
    re-file keeps every check bound to it. If the name moved too, a worker whose
    own confirmation was discounted would re-file under a colleague and have it
    counted.
    """
    _project(tmp_path)
    payload = _state(
        tmp_path,
        "assume",
        "--by",
        "engineer:someone-else",
        "--claim",
        "C1",
        "--id",
        "rh",
        "--statement",
        "Every nontrivial zero has real part one half.",
        "--source",
        "Titchmarsh, The Theory of the Riemann Zeta-Function",
        "--source-id",
        DOI,
        "--locator",
        LOCATOR,
    )

    assert "engineer:you" in payload["filed_by"]
    result = attribute_citation(
        tmp_path,
        claim_id="C1",
        assumption_id="rh",
        excerpt=EXCERPT,
        verdict=Verdict.SUPPORTS,
        checked_by="engineer:you",
    )
    assert result["ok"] is False


def test_an_assumption_with_no_recorded_filer_is_not_downgraded(
    tmp_path: Path,
) -> None:
    """Records written before the field existed keep the reading they had.

    Failing them closed would invalidate confirmations that were obtained
    honestly and cannot now be re-obtained against a filer nobody wrote down.
    The CLI requires ``--by``, so the gap does not grow.
    """
    from argus.proof_ledger import ExternalAssumption
    from argus.proof_ledger.assessment import assess_citation
    from argus.proof_ledger.models import EvidenceRecord

    legacy = ExternalAssumption(
        assumption_id="rh",
        statement="Every nontrivial zero has real part one half.",
        source="Titchmarsh",
        source_id=DOI,
        locator=LOCATOR,
    )
    assert legacy.filed_by == ""
    record = EvidenceRecord(
        evidence_id="legacy-1",
        subject=legacy.ref(),
        tier=EvidenceTier.LITERATURE,
        verdict=Verdict.SUPPORTS,
        produced_by="whoever",
        artifact="research/literature/x.json",
    )

    assert assess_citation(legacy, [record]).status is CitationStatus.CONFIRMED
