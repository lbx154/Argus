"""Load and query the curated chip-design tool and reusable-IP registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..kernel_engineering.tool_registry import (
    filter_entries as _filter_entries,
)
from ..kernel_engineering.tool_registry import (
    load_registry as _load_registry,
)
from ..kernel_engineering.tool_registry import (
    probe_entries,
    render_catalog,
    validate_registry,
)

REGISTRY_PATH = Path(__file__).with_name("references") / "specialized_tool_registry.json"


def load_registry(path: Path | None = None) -> dict[str, Any]:
    return _load_registry(path or REGISTRY_PATH)


def filter_entries(
    registry: dict[str, Any],
    *,
    categories: Iterable[str] = (),
    platforms: Iterable[str] = (),
    query: str = "",
    include_legacy: bool = False,
) -> list[dict[str, Any]]:
    return _filter_entries(
        registry,
        categories=categories,
        platforms=platforms,
        query=query,
        include_legacy=include_legacy,
    )


__all__ = [
    "REGISTRY_PATH",
    "filter_entries",
    "load_registry",
    "probe_entries",
    "render_catalog",
    "validate_registry",
]
