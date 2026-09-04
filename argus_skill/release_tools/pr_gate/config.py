from __future__ import annotations

import json
from pathlib import Path
from typing import Any

KNOWN_CRITERIA = {
    "scope_adequacy",
    "file_type_consistency",
    "task_type_alignment",
}


def load_config(path: Path) -> dict[str, dict[str, Any]]:
    criteria = json.loads(path.read_text(encoding="utf-8")).get("criteria")
    if not isinstance(criteria, dict):
        raise ValueError("config must contain a criteria object")

    unknown = set(criteria) - KNOWN_CRITERIA
    if unknown:
        raise ValueError(f"unknown criteria: {', '.join(sorted(unknown))}")

    for name, settings in criteria.items():
        if not isinstance(settings, dict):
            raise ValueError(f"{name} config must be an object")
        if not isinstance(settings.get("enabled"), bool):
            raise ValueError(f"{name}.enabled must be boolean")
        if not isinstance(settings.get("uses_llm"), bool):
            raise ValueError(f"{name}.uses_llm must be boolean")
        if not isinstance(settings.get("error_message"), str) or not settings["error_message"]:
            raise ValueError(f"{name}.error_message must be a non-empty string")
        threshold = settings.get("threshold")
        if settings["enabled"] and not settings["uses_llm"]:
            if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
                raise ValueError(f"{name}.threshold must be between 0 and 1")

    return criteria
