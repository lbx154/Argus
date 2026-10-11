"""Optional application chrome; never a source of runtime or project authority."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

ASSET_NAMES = frozenset({"mark", "mark_dark", "logo", "logo_dark", "icon", "icon_dark"})
DEFAULT_BRAND = {
    "schema": 1,
    "id": "argus",
    "name": "Argus",
    "tagline": "Autonomous Work Lab",
    "terminal_wordmark": "argus",
    "assets": {},
}


@dataclass(frozen=True)
class ApplicationBrand:
    public: dict
    assets: dict[str, tuple[bytes, str]]


def _bounded_read(path: Path, limit: int) -> bytes:
    if not path.is_file():
        raise ValueError(f"Application brand file is not a regular file: {path.name}")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError(f"Application brand file exceeds {limit} bytes: {path.name}")
    return content


def load_application_brand() -> ApplicationBrand:
    configured = os.environ.get("ARGUS_APPLICATION_BRAND", "").strip()
    if not configured:
        return ApplicationBrand(dict(DEFAULT_BRAND), {})
    manifest = Path(configured).expanduser()
    if not manifest.is_absolute() or manifest.resolve() != manifest:
        raise ValueError("ARGUS_APPLICATION_BRAND must be an absolute, non-symlink manifest")
    data = json.loads(_bounded_read(manifest, 16384))
    if (
        not isinstance(data, dict) or set(data) != set(DEFAULT_BRAND)
        or type(data["schema"]) is not int or data["schema"] != 1
    ):
        raise ValueError("Invalid application brand schema")
    for key, limit in (("id", 40), ("name", 48), ("tagline", 100), ("terminal_wordmark", 24)):
        value = data[key]
        if not isinstance(value, str) or not value.strip() or len(value) > limit or not value.isprintable():
            raise ValueError(f"Invalid application brand {key}")
    if not re.fullmatch(r"[a-z][a-z0-9-]*", data["id"]) or data["id"] == "argus":
        raise ValueError("Custom application brand needs a distinct lowercase id")
    if not re.fullmatch(r"[a-z0-9 -]+", data["terminal_wordmark"]):
        raise ValueError("Invalid terminal wordmark")
    paths = data["assets"]
    if not isinstance(paths, dict) or set(paths) != ASSET_NAMES:
        raise ValueError("Application brand requires light/dark mark, logo and icon assets")
    assets = {}
    for name, relative in paths.items():
        if not isinstance(relative, str) or not relative:
            raise ValueError(f"Invalid application brand asset: {name}")
        path = manifest.parent / relative
        if (
            Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or path.resolve() != path
            or not path.is_relative_to(manifest.parent)
            or path.suffix not in {".svg", ".png"}
        ):
            raise ValueError(f"Application brand asset must be a local SVG/PNG: {name}")
        content = _bounded_read(path, 262144)
        if not content:
            raise ValueError(f"Empty application brand asset: {name}")
        assets[name] = (content, "image/svg+xml" if path.suffix == ".svg" else "image/png")
    return ApplicationBrand(
        {**data, "assets": {name: f"/api/application-brand/assets/{name}" for name in assets}},
        assets,
    )
