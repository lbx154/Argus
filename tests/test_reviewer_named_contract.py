from __future__ import annotations

from pathlib import Path

import argus.reviewer._core as reviewer_core


def test_retired_reviewer_output_schema_assets_are_absent() -> None:
    package = Path(reviewer_core.__file__).resolve().parent
    assert list(package.glob("reviewer*_schema.json")) == []
