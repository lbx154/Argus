"""Two layers of citation checking, run out of band and blocking at delivery.

A proof that leans on Theorem 3.2 of some paper has imported a risk no compiler
sees and no reviewer of the proof itself will catch: the paper may not exist,
or it may exist and contain no Theorem 3.2, or contain one that says something
else. The principles document names this as the failure that survives every
retrieval harness, and it is the reason ``ExternalAssumption`` carries a
``source_id`` and a ``locator`` at all — without them there is nothing a checker
could be sent to look at.

**Two layers, because they fail differently.**

*Existence* is mechanical and this module does it: a registry is asked whether
the document resolves, and the answer is archived. It is cheap, it needs no
model, and it catches the fabricated identifier — the single most common way a
generated citation is wrong. What it cannot do is confirm anything. A DOI that
resolves says a paper is there; the citation was never about the paper, it was
about a proposition inside it. So this layer's only decisive answer is
``refutes``, and a successful resolution is recorded as ``inconclusive``: the
checker ran, and the question it was asked is not the question it can settle.
Anything else would let "the paper exists" quietly clear a gate that is asking
"and does it say this".

*Attribution* is the question that matters and it ends in somebody reading. The
reader supplies the passage they read; it is archived; their verdict is recorded
against it. Nothing here can verify that the passage is genuine — that is not a
gap this design can close, and pretending otherwise would be the dishonesty the
tier system exists to prevent. What it does close is the unexaminable verdict:
a confirmation now points at the text it was reached from, so the next reader
can disagree with it, which is exactly what ``ARTIFACT_REQUIRED_TIERS`` asks of
this tier.

**Asynchronous by construction, blocking only at delivery.** There is no queue
file and no daemon. ``status`` derives what is outstanding from the state
itself, so a checker can be started at any time, by any worker, in any order,
and nothing it does interrupts the reasoning in progress — a check that lands
while ``solve`` is running changes a status and stops no one. The block is at
the end, in ``stages.stage_completion_issues`` for the ``review`` stage only:
nothing ships until every citation is either confirmed against an archived
passage or honestly marked as citing prose.

**Several workers at once, and no lock.** Retrieved material is archived under
``research/literature/`` at a path derived from its own content, so two workers
who retrieve the same passage write identical bytes to one path and two who
retrieve different passages write to different ones. There is no shared mutable
ledger of literature to serialize on, which is why the concurrency problem this
would otherwise have does not arise. The state file is still shared and mutable,
and the write into it still goes through ``locked_state``.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...proof_ledger import (
    CitationStatus,
    ExternalAssumption,
    MathStateError,
    Verdict,
    load_state,
    normalize_text,
)
from .math_state import record_citation_evidence

__all__ = [
    "DELIVERABLE_STATUSES",
    "Resolution",
    "attribute_citation",
    "main",
    "resolve_citation",
    "resolve_source",
]

#: What a fetcher returns: the HTTP status, and the body as text.
Fetcher = Callable[[str], "tuple[int, str]"]

#: How long a registry gets before it counts as unreachable. Short on purpose —
#: a lookup that hangs is a lookup that stops being run.
_TIMEOUT_SECONDS = 15.0

#: Registries reach conclusions about identifiers, not about repositories, so
#: the endpoint is named in the archive and in ``produced_by``. Two registries
#: answering about one citation are two checkers; the same one answering twice
#: is one.
_ARXIV_ENDPOINT = "https://export.arxiv.org/api/query?id_list={id}"
_DOI_ENDPOINT = "https://doi.org/api/handles/{id}"

#: Bodies below this are not passages. A theorem statement worth citing does not
#: fit in a line, and an "excerpt" that does is a reader asserting rather than
#: quoting — which is what ``judgement`` is for.
_MIN_EXCERPT_CHARS = 40

_USER_AGENT = "argus-citation-check/1 (+research math evidence checking)"

#: ``produced_by`` of a lookup this program performed itself. Refused from
#: ``--by`` so a reader's word cannot be filed as a registry's answer: the two
#: are grouped on verbatim as independent checkers, and one of them is a
#: program that either reached a registry or did not.
_RESOLVER_PREFIX = "citation_check/"

#: The states delivery is allowed to happen in. Narrower than
#: ``SETTLED_CITATION_STATUSES``, and the difference is ``disputed``: a checker
#: who went and found the proposition missing has finished their work, so the
#: citation owes no further *retrieval* — but a project must not ship standing
#: on a source that does not contain what it is said to contain. Settled is a
#: question about the queue; this is a question about the result.
DELIVERABLE_STATUSES = frozenset({CitationStatus.CONFIRMED, CitationStatus.UNCITED})


# -- layer one: does the document resolve ------------------------------------

@dataclass(frozen=True)
class Resolution:
    """One registry's answer about one identifier.

    ``outcome`` is deliberately four-valued rather than a boolean. "The registry
    says no such handle" and "the registry did not answer" are opposite facts
    about the world that a boolean would merge, and merging them is how an
    offline machine reports every citation as fabricated.
    """

    source_id: str
    registry: str
    endpoint: str
    outcome: str
    detail: str
    body: str = ""

    @property
    def verdict(self) -> Verdict:
        """``refutes`` only for an authoritative negative.

        ``present`` is ``inconclusive`` because the citation names a proposition
        and this layer checked a document. ``unreachable`` and ``unsupported``
        are ``inconclusive`` because a checker that could not run has not found
        anything — and ``refutes`` is in ``REFUTING_TIERS`` for the literature
        tier, so getting this wrong would let a firewall mark a real paper
        fabricated.
        """
        return Verdict.REFUTES if self.outcome == "absent" else Verdict.INCONCLUSIVE

    @property
    def produced_by(self) -> str:
        return f"{_RESOLVER_PREFIX}{self.registry}"

    def envelope(self) -> dict[str, Any]:
        return {
            "kind": "resolution",
            "registry": self.registry,
            "endpoint": self.endpoint,
            "outcome": self.outcome,
            "detail": self.detail,
            "body": self.body,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "registry": self.registry,
            "endpoint": self.endpoint,
            "outcome": self.outcome,
            "detail": self.detail,
        }


def resolve_source(source_id: str, *, fetch: Fetcher | None = None) -> Resolution:
    """Ask the registry named by the identifier's scheme whether it resolves.

    ``fetch`` is injectable so this is testable without a network and so a host
    with its own proxy can supply one. The default reaches the public registry
    and treats every failure as ``unreachable``.

    An identifier whose scheme has no registry here is ``unsupported``, never
    ``absent``. An ISBN, a private communication, a technical report with a URL
    and no DOI are all legitimate sources this layer simply cannot interrogate,
    and reporting them as missing would be this program asserting something it
    did not check.
    """
    identifier = normalize_text(source_id)
    scheme, _, rest = identifier.partition(":")
    scheme = scheme.strip().lower()
    rest = rest.strip()
    if scheme == "arxiv" and rest:
        return _resolve_arxiv(identifier, rest, fetch or _fetch)
    if scheme == "doi" and rest:
        return _resolve_doi(identifier, rest, fetch or _fetch)
    return Resolution(
        source_id=identifier,
        registry="none",
        endpoint="",
        outcome="unsupported",
        detail=(
            f"no registry in this checker answers about {scheme or 'bare'} "
            "identifiers, so existence was not checked; the attribution layer "
            "is the one that settles this citation"
        ),
    )


def _resolve_arxiv(source_id: str, rest: str, fetch: Fetcher) -> Resolution:
    endpoint = _ARXIV_ENDPOINT.format(id=urllib.parse.quote(rest, safe=""))
    status, body = _try(fetch, endpoint)
    if status <= 0:
        return _unreachable(source_id, "arxiv", endpoint, body)
    # The API answers 200 for a well-formed request about a paper that does not
    # exist, and says so in the feed rather than in the status line: an entry
    # titled "Error" for a malformed identifier, and no entry at all for a
    # well-formed one nobody has published.
    if "<entry" not in body:
        return Resolution(
            source_id, "arxiv", endpoint, "absent",
            "the arXiv API returned no entry for this identifier", body,
        )
    if "<title>Error</title>" in body:
        return Resolution(
            source_id, "arxiv", endpoint, "absent",
            "the arXiv API reports this identifier as malformed or unknown", body,
        )
    return Resolution(
        source_id, "arxiv", endpoint, "present",
        "the document resolves; nothing here has read the proposition in it",
        body,
    )


def _resolve_doi(source_id: str, rest: str, fetch: Fetcher) -> Resolution:
    endpoint = _DOI_ENDPOINT.format(id=urllib.parse.quote(rest, safe="/"))
    status, body = _try(fetch, endpoint)
    if status <= 0:
        return _unreachable(source_id, "doi.org", endpoint, body)
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return _unreachable(
            source_id, "doi.org", endpoint,
            "the handle service answered with something that is not JSON",
        )
    # 1 is success and 100 is "handle not found"; anything else is the service
    # declining to answer, which is not the same as answering no.
    code = payload.get("responseCode")
    if code == 100:
        return Resolution(
            source_id, "doi.org", endpoint, "absent",
            "the DOI handle service has no record of this identifier", body,
        )
    if code != 1:
        return _unreachable(
            source_id, "doi.org", endpoint,
            f"the handle service answered with responseCode {code!r}",
        )
    return Resolution(
        source_id, "doi.org", endpoint, "present",
        "the document resolves; nothing here has read the proposition in it",
        body,
    )


def _unreachable(
    source_id: str, registry: str, endpoint: str, detail: str
) -> Resolution:
    return Resolution(
        source_id=source_id,
        registry=registry,
        endpoint=endpoint,
        outcome="unreachable",
        detail=(
            f"{registry} did not answer, so this citation is unchecked rather "
            f"than wrong: {detail}"
        ),
    )


def _try(fetch: Fetcher, url: str) -> tuple[int, str]:
    try:
        return fetch(url)
    except Exception as exc:  # noqa: BLE001 — any failure to reach is the same fact
        return 0, f"{type(exc).__name__}: {exc}"


def _fetch(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return int(response.status or 0), response.read().decode(
                "utf-8", errors="replace"
            )
    except urllib.error.HTTPError as exc:
        # A 404 from a handle service still carries the answer in its body, so
        # the body is kept rather than discarded with the exception.
        return int(exc.code), exc.read().decode("utf-8", errors="replace")


# -- the two commands, as functions ------------------------------------------

def resolve_citation(
    project_root: Path | str,
    *,
    claim_id: str,
    assumption_id: str,
    fetch: Fetcher | None = None,
) -> dict[str, Any]:
    """Layer one, recorded. Reads the citation, asks the registry, archives."""
    assumption = _require_assumption(project_root, claim_id, assumption_id)
    resolution = resolve_source(assumption.source_id, fetch=fetch)
    recording = record_citation_evidence(
        project_root,
        claim_id=claim_id,
        assumption_id=assumption_id,
        verdict=resolution.verdict,
        produced_by=resolution.produced_by,
        retrieval=resolution.envelope(),
    )
    return {
        "ok": not recording.refusals,
        "layer": "existence",
        "resolution": resolution.as_dict(),
        **recording.as_dict(),
    }


def attribute_citation(
    project_root: Path | str,
    *,
    claim_id: str,
    assumption_id: str,
    excerpt: str,
    verdict: Verdict,
    checked_by: str,
    note: str = "",
) -> dict[str, Any]:
    """Layer two, recorded. The passage is archived before the verdict is.

    A refutation needs an excerpt as much as a confirmation does, and that is
    not an oversight about quoting an absence: what the reader supplies is
    whatever they found where the citation pointed — the theorem that actually
    carries that number, the numbering around the gap. "It is not there" with
    nothing attached is an opinion, and the tier for opinions is ``judgement``.

    A supporting verdict from the assumption's own filer is refused here as well
    as discounted at read time. The read-side rule in ``assess_citation`` is the
    gate — it holds against a record written by any route, including a hand
    edit — and this is the error message: refusing at the point of the mistake
    says which reader is needed while the worker is still standing there, rather
    than leaving a citation that reads ``self_checked`` for whoever runs
    ``status`` next. Refutations and inconclusive answers from the filer are
    recorded normally; those are reports against interest.
    """
    assumption = _require_assumption(project_root, claim_id, assumption_id)
    filer = normalize_text(assumption.filed_by)
    if verdict == Verdict.SUPPORTS and filer and normalize_text(checked_by) == filer:
        return {
            "ok": False,
            "layer": "attribution",
            "recorded": None,
            "refusals": [
                f"{checked_by!r} filed this assumption, so this confirms a "
                "citation you wrote. The reading of the source is the thing "
                "under review, and its author's answer is that reading again, "
                "not a check of it — record it under the reader who actually "
                "went and looked. A refutation or an inconclusive answer from "
                "you is recorded normally"
            ],
        }
    if normalize_text(checked_by).startswith(_RESOLVER_PREFIX):
        return {
            "ok": False,
            "layer": "attribution",
            "recorded": None,
            "refusals": [
                f"{checked_by!r} is reserved for the lookups this program "
                "performs itself; a reader's verdict filed under it would "
                "read as a registry's answer, and the two are counted as "
                "separate checkers"
            ],
        }
    body = excerpt.strip()
    if len(body) < _MIN_EXCERPT_CHARS:
        return {
            "ok": False,
            "layer": "attribution",
            "recorded": None,
            "refusals": [
                f"the excerpt is {len(body)} characters; a passage that settles "
                "whether a source states a proposition does not fit in "
                f"{_MIN_EXCERPT_CHARS}, so this would archive an assertion "
                "rather than a quotation. Quote what you read — for a "
                "refutation, quote what you found where the citation pointed"
            ],
        }
    payload: dict[str, Any] = {"kind": "excerpt", "text": body}
    if normalize_text(note):
        payload["note"] = note.strip()
    recording = record_citation_evidence(
        project_root,
        claim_id=claim_id,
        assumption_id=assumption_id,
        verdict=verdict,
        produced_by=checked_by,
        retrieval=payload,
    )
    return {
        "ok": not recording.refusals,
        "layer": "attribution",
        **recording.as_dict(),
    }


def citation_status(project_root: Path | str) -> dict[str, Any]:
    """What every citation in the project currently owes, by claim.

    Derived, never stored. This is the work list an out-of-band checker reads,
    and deriving it is what lets several of them run at once without a queue to
    contend over: two workers that pick the same citation write the same
    archive and the same record, and the second one changes nothing.
    """
    state = load_state(project_root)
    claims: dict[str, list[dict[str, Any]]] = {}
    for claim in state.current_claims():
        assessments = state.citations(claim.claim_id)
        if assessments:
            claims[claim.claim_id] = [item.as_dict() for item in assessments]
    blocking = {
        claim_id: [
            item
            for item in items
            if item["status"] not in {s.value for s in DELIVERABLE_STATUSES}
        ]
        for claim_id, items in claims.items()
    }
    blocking = {claim_id: items for claim_id, items in blocking.items() if items}
    return {"ok": not blocking, "claims": claims, "blocking": blocking}


def _require_assumption(
    project_root: Path | str, claim_id: str, assumption_id: str
) -> ExternalAssumption:
    state = load_state(project_root)
    for item in state.effective_assumptions(claim_id):
        if item.assumption_id == assumption_id:
            return item
    raise MathStateError(
        f"claim {claim_id!r} does not stand on an assumption {assumption_id!r}"
    )


# -- CLI ---------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        child = sub.add_parser(name, help=help_text)
        child.add_argument("--project-root", type=Path, default=Path("."))
        return child

    add("status", "report what every citation owes, and what blocks delivery")

    resolve = add(
        "resolve",
        "ask the registry whether the cited document exists (layer one)",
    )
    resolve.add_argument("--claim", required=True)
    resolve.add_argument(
        "--assumption",
        required=True,
        help="the assumption id whose --source-id is looked up",
    )

    attribute = add(
        "attribute",
        "record what a reader found at the cited proposition (layer two)",
    )
    attribute.add_argument("--claim", required=True)
    attribute.add_argument("--assumption", required=True)
    attribute.add_argument(
        "--excerpt-file",
        required=True,
        type=Path,
        help=(
            "a file holding the passage you read at that locator. It is "
            "archived under research/literature/ and the verdict is recorded "
            "against it, so a later reader can disagree with you"
        ),
    )
    attribute.add_argument(
        "--verdict",
        required=True,
        choices=[item.value for item in Verdict],
        help=(
            "supports: the source states this proposition. refutes: it does "
            "not, and the excerpt says what is there instead. inconclusive: "
            "you reached the source and could not settle it"
        ),
    )
    attribute.add_argument(
        "--by",
        required=True,
        help=(
            "who read it. Grouped on verbatim as the independence key, so two "
            "names are two checkers and one name twice is one"
        ),
    )
    attribute.add_argument("--note", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    root = args.project_root.expanduser().resolve()

    try:
        if args.command == "status":
            payload = citation_status(root)
        elif args.command == "resolve":
            payload = resolve_citation(
                root, claim_id=args.claim, assumption_id=args.assumption
            )
        else:
            payload = attribute_citation(
                root,
                claim_id=args.claim,
                assumption_id=args.assumption,
                excerpt=args.excerpt_file.read_text(encoding="utf-8"),
                verdict=Verdict(args.verdict),
                checked_by=args.by,
                note=args.note,
            )
    except (MathStateError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
