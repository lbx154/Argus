"""fiction_writing runtime STYLE + TEMPORAL + NOVELTY gates — one consolidated CLI.

    style-lint     fiction/draft.md [fiction/style_profile.json] [fiction/creative_brief.json]
    temporal-check fiction/story_state.json
    novelty-check  fiction/draft.md fiction/reference_text.md [fiction/style_profile.json] [fiction/creative_brief.json]

All are DETERMINISTIC review-stage gates run as subprocesses (wired into
STAGE_CHECKS), so — unlike the reviewer's heuristic craft notes — their verdict
CANNOT be faked by the writing agent (the subprocess recomputes from the files on
disk). Deliberately thin, in the honest spirit of ``modern_poetry``'s form-check:

* ``style-lint`` fails only on an explicit ``forbidden_lexicon`` constraint.
  Anti-AI word-list hits are advisory Reviewer cues and never fail the stage.
* ``temporal-check`` fails on a deterministic age/year contradiction over
  ``story_state`` (see :mod:`.temporal`).
* ``novelty-check`` fails on a long VERBATIM run copied from the source text (the
  '不能抄' hard line — a run this long in both texts is a fact; see :mod:`.novelty`).
  It is the '不能抄' twin of style-lint's '不AI味'. Absent a reference text (an
  original, not a continuation) it passes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .novelty import check_novelty
from .style import StyleProfileError, validate_voice_card
from .style_lint import check_style
from .temporal import TemporalError, check_temporal_consistency

_ERRORS = (StyleProfileError, TemporalError)


def _load_json(path: str | None) -> dict:
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StyleProfileError(f"{path} is not valid JSON: {exc}") from exc
    return obj if isinstance(obj, dict) else {}


def _cmd_style_lint(a: argparse.Namespace) -> int:
    text = Path(a.draft).read_text(encoding="utf-8")
    card = _load_json(a.style_profile)
    if card:
        validate_voice_card(card)  # a malformed card fails the gate loudly
    brief = _load_json(a.brief)
    language = (card.get("meta") or {}).get("language") or brief.get("language") or "zh"
    findings = check_style(text, card, language)
    blocking = [f for f in findings if f["blocking"]]
    for f in findings:
        tag = "FAIL" if f["blocking"] else "note"
        print(f"  {tag} [{f['cliche_class']}] L{f['line']}: {f['detail']}", file=sys.stderr)
    if blocking:
        return 1
    notes = len(findings)
    print(f"OK: style lint clean of hard violations "
          f"({notes} non-blocking note(s); tables are model-seed / BCC-pending)")
    return 0


def _cmd_temporal_check(a: argparse.Namespace) -> int:
    state = _load_json(a.state)
    findings = check_temporal_consistency(state)
    for f in findings:
        print(f"  FAIL [{f['type']}] {f['detail']}", file=sys.stderr)
    if findings:
        return 1
    print("OK: no age/timeline contradictions")
    return 0


def _cmd_novelty_check(a: argparse.Namespace) -> int:
    ref_path = Path(a.reference)
    if not ref_path.is_file():
        print("OK: no reference_text.md present — an original, nothing to copy from")
        return 0
    reference = ref_path.read_text(encoding="utf-8")
    text = Path(a.draft).read_text(encoding="utf-8")
    card = _load_json(a.style_profile)
    if card:
        validate_voice_card(card)  # a malformed card fails the gate loudly
    brief = _load_json(a.brief)
    language = (card.get("meta") or {}).get("language") or brief.get("language") or "zh"
    findings = check_novelty(text, reference, card, language)
    blocking = [f for f in findings if f["blocking"]]
    for f in findings:
        tag = "FAIL" if f["blocking"] else "note"
        loc = f"L{f['line']}" if f.get("line") else "-"
        print(f"  {tag} [{f['type']}] {loc}: {f['detail']}", file=sys.stderr)
    if blocking:
        return 1
    print(f"OK: no verbatim copying over threshold ({len(findings)} non-blocking "
          f"note(s); run/ratio thresholds are model-seed / BCC-pending)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fiction-style-check")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("style-lint", help="anti-AI cliché + forbidden-lexicon lint")
    p.add_argument("draft")
    p.add_argument("style_profile", nargs="?")
    p.add_argument("brief", nargs="?")
    p.set_defaults(func=_cmd_style_lint)

    p = sub.add_parser("temporal-check", help="deterministic age/year consistency")
    p.add_argument("state")
    p.set_defaults(func=_cmd_temporal_check)

    p = sub.add_parser(
        "novelty-check", help="deterministic verbatim-overlap (anti-copy) gate")
    p.add_argument("draft")
    p.add_argument("reference")
    p.add_argument("style_profile", nargs="?")
    p.add_argument("brief", nargs="?")
    p.set_defaults(func=_cmd_novelty_check)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except OSError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except _ERRORS as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
