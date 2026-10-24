import json

import pytest
from fastapi.testclient import TestClient

from argus.core.application_brand import DEFAULT_BRAND, load_application_brand
from argus.webapi.server import create_app


@pytest.fixture
def brand_file(tmp_path, monkeypatch):
    folder = tmp_path / "brand"
    folder.mkdir()
    (folder / "mark.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>')
    data = {
        **DEFAULT_BRAND, "id": "example", "name": "Example", "terminal_wordmark": "example",
        "assets": {key: "mark.svg" for key in ("mark", "mark_dark", "logo", "logo_dark", "icon", "icon_dark")},
    }
    path = folder / "brand.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("ARGUS_APPLICATION_BRAND", str(path))
    return path


def test_real_app_serves_only_selected_public_bytes(brand_file, tmp_path, monkeypatch):
    client = TestClient(create_app(global_root=tmp_path / "state"))
    metadata = client.get("/api/application-brand")
    assert metadata.status_code == 200
    assert metadata.headers["cache-control"] == "no-store"
    assert metadata.json()["name"] == "Example"
    assert str(brand_file.parent) not in metadata.text
    asset = client.get(metadata.json()["assets"]["mark"])
    assert asset.content == (brand_file.parent / "mark.svg").read_bytes()
    assert asset.headers["content-type"].startswith("image/svg+xml")
    assert "sandbox" in asset.headers["content-security-policy"]
    (brand_file.parent / "mark.svg").write_text("changed after startup")
    assert client.get("/api/application-brand/assets/mark").content == asset.content
    assert client.get("/api/application-brand/assets/brand.json").status_code == 404
    assert client.get("/api/application-brand/manifest.webmanifest").json()["name"] == "Example"
    assert client.get("/api/meta").json()["runtime"]["state_root"] == str(tmp_path / "state")
    monkeypatch.delenv("ARGUS_APPLICATION_BRAND")
    other = TestClient(create_app(global_root=tmp_path / "other"))
    assert other.get("/api/application-brand").json()["id"] == "argus"
    assert client.get("/api/application-brand").json()["id"] == "example"


@pytest.mark.parametrize("mutation", [
    {"schema": True}, {"id": "argus"}, {"name": "\x1b[31mspoof"}, {"terminal_wordmark": "\nspoof"},
    {"assets": {}}, {"extra": "unsupported"},
])
def test_invalid_explicit_brand_is_an_error(brand_file, mutation):
    data = json.loads(brand_file.read_text())
    data.update(mutation)
    brand_file.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_application_brand()


@pytest.mark.parametrize("path", ["../private.svg", "/private.svg", "https://example.com/logo.svg", "brand.json"])
def test_assets_cannot_escape_or_expose_other_file_types(brand_file, path):
    data = json.loads(brand_file.read_text())
    data["assets"]["mark"] = path
    brand_file.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_application_brand()


def test_symlink_and_oversized_asset_are_rejected(brand_file):
    asset = brand_file.parent / "mark.svg"
    original = asset.read_bytes()
    other = brand_file.parent / "other.svg"
    other.write_bytes(original)
    asset.unlink()
    try:
        asset.symlink_to(other)
    except OSError:
        pytest.skip("Symlinks unavailable on this platform")
    with pytest.raises(ValueError, match="local SVG/PNG"):
        load_application_brand()
    asset.unlink()
    asset.write_bytes(b"x" * 262145)
    with pytest.raises(ValueError, match="exceeds"):
        load_application_brand()
