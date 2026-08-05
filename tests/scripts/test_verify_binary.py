from __future__ import annotations

import pytest

from scripts.verify_binary import _validate_api_meta


def _meta() -> dict:
    return {
        "service": "argus-skill-webapi",
        "protocol": {"name": "argus.webapi", "major": 1, "minor": 12},
        "capabilities": ["release.identity.v1"],
        "runtime": {"release_id": "0.1.1+example"},
    }


def test_binary_web_smoke_accepts_current_meta_contract() -> None:
    payload = _meta()
    assert _validate_api_meta(payload) is payload


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda row: row.update(service="other"), "unexpected service"),
        (lambda row: row.update(protocol={"name": "other"}), "invalid protocol"),
        (lambda row: row.update(runtime={}), "release identity"),
        (lambda row: row.update(capabilities=[]), "release.identity.v1"),
    ],
)
def test_binary_web_smoke_rejects_incomplete_meta(mutate, message: str) -> None:
    payload = _meta()
    mutate(payload)
    with pytest.raises(RuntimeError, match=message):
        _validate_api_meta(payload)
