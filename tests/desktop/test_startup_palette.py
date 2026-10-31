"""Keep the launcher, first native paint and cockpit's warm palette aligned."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop-tauri"


def _palette(path: Path, theme: str = "light") -> dict[str, str]:
    source = path.read_text(encoding="utf-8")
    selector = r":root\s*\{" if theme == "light" else r":root\[data-theme=['\"]dark['\"]\]\s*\{"
    block = re.search(selector + r"([^}]+)\}", source)
    assert block, f"Missing {theme} palette"
    return {name: value.strip() for name, value in re.findall(r"--([\w-]+):\s*([^;]+);", block[1])}


def _hex(value: str) -> tuple[int, ...]:
    assert re.fullmatch(r"#[0-9a-f]{6}", value)
    return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))


def _rgb(value: str) -> tuple[int, ...]:
    return tuple(int(channel) for channel in value.split())


def test_light_startup_uses_existing_pale_yellow_and_text_safe_gold():
    shell = _palette(DESKTOP / "src/style.css")
    cockpit = _palette(ROOT / "frontend/web/src/index.css")
    assert _hex(shell["bg"]) == _rgb(cockpit["surface-0"])
    assert cockpit["surface-1"] == cockpit["surface-0"]
    assert cockpit["chrome-surface"] == "var(--surface-1)"
    assert shell["chrome-bg"] == shell["chrome-border"] == shell["bg"]
    assert shell["tile"] != "#ffffff"
    assert _hex(shell["accent"]) == _rgb(cockpit["accent-ink"])
    assert _hex(shell["brand-eye"]) == _rgb(cockpit["brand-eye"])
    assert _hex(shell["brand-body"]) == _rgb(cockpit["brand-body"])


def test_config_native_background_and_caption_do_not_flash_cold_white():
    shell = _palette(DESKTOP / "src/style.css")
    config = json.loads((DESKTOP / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    assert config["app"]["windows"][0]["backgroundColor"] == shell["bg"]
    red, green, blue = _hex(shell["bg"])
    host = (DESKTOP / "src-tauri/src/lib.rs").read_text(encoding="utf-8")
    assert f"Some(Color({red}, {green}, {blue}, 255))" in host
    assert host.count(f"colorref({red}, {green}, {blue})") == 2
    styles = (DESKTOP / "src/style.css").read_text(encoding="utf-8")
    splash = re.search(r"\.splash\s*\{([^}]+)\}", styles)
    assert splash and "background: var(--bg);" in splash[1]


def test_dark_startup_keeps_the_cockpit_dark_surface_and_gold():
    shell = _palette(DESKTOP / "src/style.css", "dark")
    cockpit = _palette(ROOT / "frontend/web/src/index.css", "dark")
    assert _hex(shell["bg"]) == _rgb(cockpit["surface-0"])
    assert _hex(shell["tile"]) == _rgb(cockpit["surface-1"])
    assert _hex(shell["accent"]) == _rgb(cockpit["accent"])
    host = (DESKTOP / "src-tauri/src/lib.rs").read_text(encoding="utf-8")
    assert "Some(Color(12, 12, 13, 255))" in host
    assert host.count("colorref(21, 21, 23)") == 2


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_completed_step_foreground_remains_readable_on_warm_accents(theme):
    palette = _palette(DESKTOP / "src/style.css", theme)

    def luminance(color):
        channels = [channel / 255 for channel in _hex(color)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    foreground, background = sorted((luminance(palette["on-accent"]), luminance(palette["success"])))
    assert (background + 0.05) / (foreground + 0.05) >= 4.5
