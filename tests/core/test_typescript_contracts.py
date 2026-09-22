"""Both runtimes consume these fixtures; Python remains the production owner."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from argus.core.contract_resources import contract_schema_path
from argus.core.event_catalog import validate_event_envelope
from argus.webapi.protocol import (
    API_CAPABILITIES,
    API_PROTOCOL_MAJOR,
    API_PROTOCOL_MINOR,
    API_PROTOCOL_NAME,
    API_SERVICE,
    SNAPSHOT_SCHEMA_VERSION,
)

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "packages/contracts/fixtures/event-validation.json").read_text())


@pytest.mark.parametrize("fixture", CASES, ids=lambda fixture: fixture["id"])
def test_shared_event_validation(fixture: dict) -> None:
    options = fixture["options"]
    result = validate_event_envelope(
        fixture["event"],
        require_known=options.get("requireKnown", False),
        allow_missing_fields=options.get("allowMissingFields", False),
    )
    assert {
        "valid": result.valid,
        "known": result.known,
        "canonical_type": result.canonical_type,
    } == fixture["expected"]


def test_api_protocol_comes_from_the_shared_contract() -> None:
    contract = json.loads(contract_schema_path("api_protocol.json").read_text())
    assert contract == {
        "API_SERVICE": API_SERVICE,
        "API_PROTOCOL_NAME": API_PROTOCOL_NAME,
        "API_PROTOCOL_MAJOR": API_PROTOCOL_MAJOR,
        "API_PROTOCOL_MINOR": API_PROTOCOL_MINOR,
        "SNAPSHOT_SCHEMA_VERSION": SNAPSHOT_SCHEMA_VERSION,
        "API_CAPABILITIES": list(API_CAPABILITIES),
    }


@pytest.mark.parametrize("name", ["api_protocol.json", "model_pricing.json"])
def test_installed_schema_location_takes_precedence(tmp_path: Path, monkeypatch, name: str) -> None:
    from argus.core import contract_resources

    module = tmp_path / "argus/core/contract_resources.py"
    resource = tmp_path / "argus/_contracts" / name
    resource.parent.mkdir(parents=True)
    resource.write_text("{}")
    monkeypatch.setattr(contract_resources, "__file__", str(module))
    assert contract_resources.contract_schema_path(name) == resource


def test_shared_sources_participate_in_release_identity(tmp_path: Path) -> None:
    from argus.release import compute_source_digest

    schema = tmp_path / "packages/contracts/schemas/api_protocol.json"
    source = tmp_path / "packages/runtime/src/pi.ts"
    generated_build = tmp_path / "packages/runtime/dist/pi.js"
    for path in (schema, source, generated_build):
        path.parent.mkdir(parents=True, exist_ok=True)
    schema.write_text("{}")
    source.write_text("export const version = 1;")
    first = compute_source_digest(tmp_path)
    generated_build.write_text("ignored build output")
    assert compute_source_digest(tmp_path) == first
    source.write_text("export const version = 2;")
    second = compute_source_digest(tmp_path)
    assert second != first
    schema.write_text('{"major": 2}')
    assert compute_source_digest(tmp_path) != second


def test_sdist_keeps_contracts_when_npm_workspace_links_exist(tmp_path: Path) -> None:
    from hatchling.builders.sdist import SdistBuilder

    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]["sdist"]
    lines = ['[project]', 'name = "argus-fixture"', 'version = "0.0.0"', '[tool.hatch.build.targets.sdist]']
    lines.extend(f"{key} = {json.dumps(value)}" for key, value in config.items())
    (tmp_path / "pyproject.toml").write_text("\n".join(lines))
    package = tmp_path / "packages/contracts"
    schema = package / "schemas/api_protocol.json"
    schema.parent.mkdir(parents=True)
    schema.write_text("{}")
    compiled = package / "dist/index.js"
    compiled.parent.mkdir()
    compiled.write_text("export {};")
    link = tmp_path / "node_modules/@argus/contracts"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(package, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    names = {item.relative_path.replace("\\", "/") for item in SdistBuilder(str(tmp_path)).recurse_included_files()}
    assert "packages/contracts/schemas/api_protocol.json" in names
    assert not any("node_modules" in name or "/dist/" in name for name in names)
