"""The approved Windows icon changes contrast, not the established geometry."""
from __future__ import annotations

import json
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ICONS = ROOT / "desktop-tauri/src-tauri/icons"
SIZES = {16, 24, 32, 48, 64, 128, 256}


def test_active_desktop_icon_uses_the_approved_rounded_square_not_the_old_circle():
    config = json.loads((ICONS.parent / "tauri.conf.json").read_text(encoding="utf-8"))
    expected = "icons/icon-light-rounded.ico"
    assert config["bundle"]["icon"] == [expected]
    assert config["bundle"]["windows"]["nsis"]["installerIcon"] == expected
    assert config["bundle"]["windows"]["nsis"]["uninstallerIcon"] == expected


def test_light_icon_preserves_every_shape_and_only_changes_contrast():
    original = ET.parse(ROOT / "docs/assets/brand/svg/argus-mark-dark-rounded-square.svg").getroot()
    light = ET.parse(ICONS / "icon-light-rounded.svg").getroot()
    assert original.attrib["viewBox"] == light.attrib["viewBox"] == "0 0 512 512"
    def shapes(root):
        return [(node.tag, {key: value for key, value in node.attrib.items() if key != "fill"})
                for node in root if node.tag.rsplit("}", 1)[-1] != "title"]
    assert shapes(original) == shapes(light)
    ns = {"svg": "http://www.w3.org/2000/svg"}
    assert light.find("svg:rect", ns).attrib["fill"] == "#ffffff"
    assert [node.attrib["fill"] for node in light.findall("svg:path", ns)] == ["#202326", "#ffffff"]
    assert [node.attrib["fill"] for node in light.findall("svg:circle", ns)] == ["#202326", "#ffffff"]


def test_desktop_ico_contains_all_seven_native_resolution_frames():
    payload = (ICONS / "icon-light-rounded.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", payload)
    assert (reserved, kind, count) == (0, 1, len(SIZES))
    found = set()
    for index in range(count):
        width, height, _, _, _, _, size, offset = struct.unpack_from("<BBBBHHII", payload, 6 + index * 16)
        assert (width or 256) == (height or 256)
        found.add(width or 256)
        assert size > 0 and offset >= 6 + count * 16 and offset + size <= len(payload)
    assert found == SIZES
    png = (ICONS / "icon-light-rounded.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack_from(">II", png, 16) == (256, 256)
