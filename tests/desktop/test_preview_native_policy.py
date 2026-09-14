"""Native verification may inspect its own preview, never enable a release debugger."""
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


def test_developer_inspection_feature_is_scoped_to_preview_builds():
    cargo = tomllib.loads((ROOT / "src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    assert "tauri/devtools" in cargo["features"]["preview"]
    assert "devtools" not in cargo["dependencies"]["tauri"]["features"]
    source = (ROOT / "src-tauri/src/lib.rs").read_text(encoding="utf-8")
    assert ".devtools(qa_devtools)" in source
    assert "identity::preview_qa_enabled(" in source
    assert 'std::env::var("ARGUS_DESKTOP_TEST_INSTANCE")' in source
