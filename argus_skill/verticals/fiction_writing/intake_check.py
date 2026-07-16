"""fiction_writing runtime INTAKE gate — validates the shared Task Envelope at
run time.

Closes the Task-Envelope loop's runtime side: intake.py derives the private
creative_brief from the envelope, but until now the envelope contract was only
enforced by unit tests. This binds it to a STAGE_CHECK so a malformed or
mis-routed task envelope fails the intake stage at RUN TIME, uniformly with the
review / artifact / provenance gates.

Subcommand (run from the mission dir; cwd holds ``fiction/``):

    validate  fiction/task_envelope.json
        exit 0 iff the recorded task envelope is a valid shared Task Envelope AND
        is fiction-consumable (its ``form`` is a narrative-prose form). A poetry
        quatrain routed here, or an envelope missing intent / with an editing mode
        but no source reference, fails.

Exit 1 with a diagnostic on any violation, so the STAGE_CHECK fails the stage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ...literary.task_envelope import EnvelopeError
from .intake import FictionIntakeError, brief_from_envelope
from .style import StyleProfileError, validate_voice_card


def _load_json(path: str) -> object:
    p = Path(path)
    if not p.is_file():
        raise EnvelopeError(f"file not found: {path}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EnvelopeError(f"{path} is not valid JSON: {exc}") from exc


def _cmd_validate(args: argparse.Namespace) -> int:
    # brief_from_envelope normalizes+validates the shared contract, then applies
    # fiction's form check — one call proves both "valid" and "fiction-consumable".
    brief = brief_from_envelope(_load_json(args.envelope))
    print(f"OK: task envelope valid and fiction-consumable "
          f"(form={brief['form']}, mode={brief['mode']})")
    return 0


def _cmd_validate_style(args: argparse.Namespace) -> int:
    # The voice card (the SillyTavern-style '预设'). A thin card ({}) is valid; a
    # rich one with a bad enum / stray key fails the intake gate loudly.
    card = _load_json(args.style_profile)
    validate_voice_card(card)
    reg = (card.get("meta") or {}).get("register", "unset")
    forbidden = len(card.get("forbidden_lexicon") or [])
    print(f"OK: style profile (voice card) well-formed "
          f"(register={reg}, forbidden_lexicon={forbidden} term(s))")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fiction-intake-check")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("validate", help="validate fiction/task_envelope.json")
    pv.add_argument("envelope")
    pv.set_defaults(func=_cmd_validate)

    ps = sub.add_parser("validate-style", help="validate fiction/style_profile.json")
    ps.add_argument("style_profile")
    ps.set_defaults(func=_cmd_validate_style)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (EnvelopeError, FictionIntakeError, StyleProfileError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
