"""fiction_writing binding to the shared source-registry / provenance contracts.

Points the shared contract at fiction's own ``references/source_registry/
sources.yaml`` (the rights catalog for the corpora and public-domain texts this
vertical may consult) and exposes a single validated-load entry point the runtime
provenance gate and tests share.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...literary.source_registry import load_validated_registry

#: Fiction's rights catalog — the two-layer providers+items registry.
FICTION_SOURCE_REGISTRY_PATH: Path = (
    Path(__file__).resolve().parent
    / "references" / "source_registry" / "sources.yaml"
)


def load_fiction_registry() -> dict[str, Any]:
    """Load AND validate fiction's source registry; return the parsed dict.

    Raises :class:`argus_skill.literary.source_registry.RegistryError` if the
    committed registry is malformed — so a broken registry fails the intake gate.
    """
    return load_validated_registry(FICTION_SOURCE_REGISTRY_PATH)


__all__ = [
    "FICTION_SOURCE_REGISTRY_PATH",
    "load_fiction_registry",
]
