"""The committed fiction source registry is valid."""
from __future__ import annotations

from argus_skill.verticals.fiction_writing.sources import load_fiction_registry


def test_committed_fiction_registry_is_valid():
    reg = load_fiction_registry()  # loads + validates the real sources.yaml
    assert reg.get("items"), "registry should list at least one source item"
