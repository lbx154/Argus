"""The Task Envelope — shared literary-vertical creative-authoring task contract.

The single intake artifact every literary vertical (fiction, poetry, prose,
editor) normalizes a raw operator request into BEFORE any planning. It is
deliberately vertical-agnostic:

* ``mode`` is a CLOSED enum of authoring TASK TYPES (from_scratch … critique);
* ``form`` / ``genre_profile`` are free strings each vertical interprets itself
  (a fiction ``form`` is short_story/chapter; a poetry ``form`` is quatrain/…);
* ``reference_inputs`` carry the source text / prior state / style samples an
  editing or continuation task needs.

This module is NOT a vertical: it ships no ``stages`` contract and is never
registered in ``VERTICALS``. It is a contract library the verticals consume.

Two enforced entry points (neither advisory):

* :func:`validate_envelope` — structural: rejects an envelope that violates the
  JSON schema (unknown mode, missing/blank intent, bad language, stray keys).
* :func:`normalize_envelope` — fills documented defaults, validates, then
  enforces the SEMANTIC rules the schema cannot express — chiefly that an
  editing/continuation ``mode`` (which operates ON existing text) MUST carry a
  ``source_text`` / ``prior_state`` reference input, else the task is incoherent.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def _load_schema(name: str) -> dict[str, Any]:
    with (_SCHEMA_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


TASK_ENVELOPE_SCHEMA: dict[str, Any] = _load_schema("task_envelope.schema.json")

#: The closed set of authoring task types (sourced from the schema so the two
#: can never drift).
VALID_MODES: frozenset[str] = frozenset(
    TASK_ENVELOPE_SCHEMA["properties"]["mode"]["enum"]
)

#: Modes that operate ON an existing text and therefore REQUIRE a source ref.
_SOURCE_REQUIRING_MODES: frozenset[str] = frozenset(
    {"continuation", "rewrite", "expand", "polish", "proofread", "critique"}
)
_SOURCE_ROLES: frozenset[str] = frozenset({"source_text", "prior_state"})

#: Documented defaults filled by :func:`normalize_envelope` for optional fields.
_DEFAULTS: dict[str, Any] = {
    "genre_profile": "",
    "audience": "",
    "target_length": None,
    "constraints": [],
    "reference_inputs": [],
    "retrieval_policy": "none",
    "output_requirements": {},
}

_STRIP_FIELDS = ("task_id", "mode", "language", "form", "genre_profile",
                 "intent", "audience", "retrieval_policy")


class EnvelopeError(ValueError):
    """Raised when a task envelope is structurally or semantically invalid."""


def validate_envelope(env: dict[str, Any]) -> None:
    """Raise :class:`EnvelopeError` if ``env`` violates the task_envelope schema.

    Structural only — mode enum, required fields, language enum, no stray keys.
    Semantic cross-field rules are enforced by :func:`normalize_envelope`.
    """
    try:
        jsonschema.validate(env, TASK_ENVELOPE_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise EnvelopeError(f"invalid task_envelope: {exc.message}") from exc


def normalize_envelope(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a validated, default-filled copy of ``raw``.

    Steps, in order: (1) fill documented defaults for omitted optional fields;
    (2) strip surrounding whitespace on the string fields (so a blank intent is
    caught, not silently accepted); (3) structural :func:`validate_envelope`;
    (4) semantic rules the schema cannot express. The input is not mutated.

    Raises :class:`EnvelopeError` on any structural or semantic violation.
    """
    if not isinstance(raw, dict):
        raise EnvelopeError("task_envelope must be a JSON object")
    env = dict(raw)
    for key, default in _DEFAULTS.items():
        env.setdefault(key, default)
    for key in _STRIP_FIELDS:
        if isinstance(env.get(key), str):
            env[key] = env[key].strip()

    validate_envelope(env)  # structural gate first

    # Semantic rule: editing/continuation operates ON existing text — it MUST be
    # given a source. The schema can constrain a field's shape but not this
    # cross-field dependency, so it is enforced here.
    if env["mode"] in _SOURCE_REQUIRING_MODES:
        roles = {r.get("role") for r in env["reference_inputs"]}
        if not (roles & _SOURCE_ROLES):
            raise EnvelopeError(
                f"mode={env['mode']!r} operates on existing text but no "
                f"reference_inputs with role in {sorted(_SOURCE_ROLES)} was "
                f"provided"
            )
    return env


__all__ = [
    "TASK_ENVELOPE_SCHEMA",
    "VALID_MODES",
    "EnvelopeError",
    "validate_envelope",
    "normalize_envelope",
]
