from __future__ import annotations

import pytest

from argus.core.portable_filename import (
    legacy_filename_components,
    normalized_logical_identifier,
    portable_filename_component,
)


def test_windows_reserved_and_unsafe_names_are_encoded() -> None:
    assert portable_filename_component("CON", windows=True).startswith("~")
    assert portable_filename_component("team::task", windows=True).startswith("~")


def test_encoded_looking_logical_id_cannot_alias_an_old_hashed_file() -> None:
    legacy = "argus-id-" + "0" * 64

    assert portable_filename_component(legacy, windows=True) != legacy


def test_case_distinct_ids_remain_distinct_on_windows() -> None:
    first = portable_filename_component("aaa::task", windows=True)
    second = portable_filename_component("aaG::task", windows=True)

    assert first.casefold() != second.casefold()


def test_raw_encoded_looking_names_remain_legacy_lookups() -> None:
    assert legacy_filename_components("~legacy") == ("~legacy",)
    assert legacy_filename_components("team::task") == ()


def test_oversized_identifier_is_refused() -> None:
    with pytest.raises(ValueError, match="longer than 120 bytes"):
        portable_filename_component("x" * 121, windows=True)


def test_normalized_logical_identifier_collapses_unicode_equivalents() -> None:
    assert normalized_logical_identifier("é") == normalized_logical_identifier("e\u0301")
