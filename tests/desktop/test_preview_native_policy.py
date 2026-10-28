"""The native shell's policy: Tauri IPC only for itself, no web inspector in a shipped build."""
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "desktop-tauri"


def test_native_shell_csp_allows_tauri_ipc_without_widening_remote_frames():
    security = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["app"]["security"]
    for name in ("csp", "devCsp"):
        directives = {parts[0]: parts[1:] for part in security[name].split(";") if (parts := part.strip().split())}
        assert "ipc:" in directives["connect-src"]
        assert "http://ipc.localhost" in directives["connect-src"]
        assert "*" not in directives["connect-src"]
        assert "http://ipc.localhost" not in directives["frame-src"]


def test_eye_animation_has_no_menu_or_native_adjustment_entry():
    html = (ROOT / "src/index.html").read_text(encoding="utf-8")
    bridge = (ROOT / "src/bridge.ts").read_text(encoding="utf-8")
    native = (ROOT / "src-tauri/src/lib.rs").read_text(encoding="utf-8")
    assert 'data-startup-eye-motion="on"' in html
    assert 'role="menuitemradio" data-startup-eye-motion' not in html
    assert 'aria-label="眼睛动画"' not in html
    assert 'setStartupEyeMotion' not in bridge
    assert 'set_startup_eye_motion' not in native


def test_no_shipped_build_enables_the_web_inspector():
    cargo = tomllib.loads((ROOT / "src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    assert "devtools" not in json.dumps(cargo["features"])
    assert "devtools" not in cargo["dependencies"]["tauri"]["features"]
    assert ".devtools(" not in (ROOT / "src-tauri/src/lib.rs").read_text(encoding="utf-8")
