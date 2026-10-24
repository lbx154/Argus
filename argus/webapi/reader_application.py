"""Apply a saved question foundation to actual progress, in one model call."""

from __future__ import annotations

PREVIEW_VERSION = 28
PROCESS_VERSION = 1


def foundation_reference(foundation: dict) -> dict:
    if isinstance(foundation, dict) and (
        foundation.get("kind", "foundation") != "foundation" or foundation.get("parent_id")
        or foundation.get("root_id", foundation.get("id")) != foundation.get("id")
    ):
        raise ValueError("application requires a root foundation, not a clarification")
    if (not isinstance(foundation, dict) or foundation.get("state") != "complete"
            or not isinstance(foundation.get("markdown"), str) or not foundation["markdown"].strip()):
        raise ValueError("a completed question foundation is required")
    reference = {key: foundation.get(key) for key in ("id", "path", "question", "version")}
    if (any(not isinstance(reference[key], str) or not reference[key].strip()
            for key in ("id", "path", "question"))
            or type(reference["version"]) is not int or reference["version"] < 1):
        raise ValueError("invalid question foundation reference")
    return reference
