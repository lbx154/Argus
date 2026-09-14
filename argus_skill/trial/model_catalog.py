"""One operator-selected model catalog for the trial gateway and Pi registry."""
from __future__ import annotations

import os
import re
from collections.abc import Iterable

from . import DEFAULT_UPSTREAM_MODEL, MODEL

_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}\Z")


def configured_model_ids(primary: str = DEFAULT_UPSTREAM_MODEL, additional: Iterable[str] | None = None) -> tuple[str, ...]:
    """Keep the default model; extra models are explicitly enabled by the host."""
    if additional is None:
        additional = [part.strip() for part in os.environ.get("ARGUS_TRIAL_MODELS", "").split(",") if part.strip()]
    if isinstance(additional, (str, bytes)):
        raise ValueError("trial model catalog must be a list of model IDs")
    models = []
    for value in (primary, *additional):
        if not isinstance(value, str) or not _MODEL_ID.fullmatch(value) or value == MODEL:
            raise ValueError("invalid trial model ID")
        if value not in models:
            models.append(value)
    if len(models) > 64:
        raise ValueError("trial model catalog exceeds 64 entries")
    return tuple(models)


def select_model(requested: object, primary: str, allowed: tuple[str, ...]) -> str:
    if requested == MODEL:
        return primary
    if isinstance(requested, str) and requested in allowed:
        return requested
    raise ValueError("the selected model is not enabled for this trial")
