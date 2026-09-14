from __future__ import annotations

from argus.core.portable_filename import (
    legacy_hashed_filename_components,
    normalized_logical_identifier,
    portable_filename_component,
)


def test_windows_reserved_and_unsafe_names_are_encoded() -> None:
    assert portable_filename_component("CON", windows=True).startswith("~")
    assert portable_filename_component("team::task", windows=True).startswith("~")


def test_encoded_looking_logical_id_cannot_alias_an_unsafe_id() -> None:
    legacy = legacy_hashed_filename_components("team::task")[0]

    assert portable_filename_component(legacy, windows=True) != legacy


def test_case_distinct_ids_remain_distinct_on_windows() -> None:
    first = portable_filename_component("aaa::task", windows=True)
    second = portable_filename_component("aaG::task", windows=True)

    assert first.casefold() != second.casefold()


def test_oversized_identifier_keeps_legacy_lookup_component() -> None:
    component = portable_filename_component("x" * 121, windows=True)

    assert component.startswith("argus-id-")


def test_normalized_logical_identifier_collapses_unicode_equivalents() -> None:
    assert normalized_logical_identifier("é") == normalized_logical_identifier("e\u0301")
