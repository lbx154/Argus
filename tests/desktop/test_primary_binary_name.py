"""The Tauri bundler must resolve the actual Cargo binary, not the crate name."""
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[2]


def test_tauri_primary_binary_is_explicit_even_when_cli_filters_required_features():
    cargo = tomllib.loads((ROOT / "desktop-tauri/src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    assert cargo["package"]["name"] == "argus-desktop"
    assert cargo["package"]["default-run"] == "Argus"
    binary = next(item for item in cargo["bin"] if item["name"] == cargo["package"]["default-run"])
    assert binary["path"] == "src/main.rs"
    assert set(binary["required-features"]) <= set(cargo["features"]["default"])
