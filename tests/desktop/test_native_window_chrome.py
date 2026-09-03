from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAURI_ROOT = ROOT / "desktop-tauri"


def _config() -> dict:
    return json.loads((TAURI_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))


def test_windows_caption_buttons_use_a_native_non_client_frame() -> None:
    config = _config()
    window = config["app"]["windows"][0]
    security = config["app"]["security"]

    # Tauri keeps native Windows decoration instead of creating an overlay that
    # could share coordinates with the cockpit's right-side controls.
    assert window["decorations"] is True
    assert window["minWidth"] == 960
    assert window["minHeight"] == 640
    assert window["theme"] == "Light"
    assert window["backgroundColor"] == "#f9fafb"
    assert "frame-src http://127.0.0.1:*" in security["csp"]
    assert security["freezePrototype"] is True
    assert config["bundle"]["resources"]["../resources/WebView2Loader.dll"] == "WebView2Loader.dll"


def test_installer_bypasses_close_to_tray_before_replacing_files() -> None:
    config = _config()
    hooks = (TAURI_ROOT / "src-tauri" / "installer-hooks.nsh").read_text(encoding="utf-8")

    nsis = config["bundle"]["windows"]["nsis"]
    assert nsis["installerHooks"] == "installer-hooks.nsh"
    assert "NSIS_HOOK_PREINSTALL" in hooks
    assert '/IM "Argus.exe"' in hooks
    assert '/IM "argus-backend.exe"' in hooks
    assert "taskkill.exe" in hooks


def test_desktop_launches_backend_without_a_console_or_forced_setup() -> None:
    backend = (TAURI_ROOT / "src-tauri" / "src" / "backend.rs").read_text(encoding="utf-8")
    settings = (TAURI_ROOT / "src-tauri" / "src" / "settings.rs").read_text(encoding="utf-8")
    shell = (TAURI_ROOT / "src" / "main.ts").read_text(encoding="utf-8")

    process = (TAURI_ROOT / "src-tauri" / "src" / "process.rs").read_text(encoding="utf-8")

    assert "CREATE_NO_WINDOW" in backend
    assert "process.creation_flags(CREATE_NO_WINDOW)" in backend
    assert 'Command::new("taskkill")' in process
    assert 'Command::new("tasklist")' in process
    assert "creation_flags(CREATE_NO_WINDOW)" in process
    assert "mandatory launcher wizard" in settings
    assert "First-run preferences are optional" in shell


def test_ready_cockpit_path_avoids_settings_discovery_and_duplicate_reload() -> None:
    shell = (TAURI_ROOT / "src" / "main.ts").read_text(encoding="utf-8")
    ready_path = shell.split("async function handleReady", 1)[1].split(
        "function runnerDescription", 1
    )[0]

    assert "desktopBridge.openCockpit()" in ready_path
    assert "desktopBridge.getSetup()" not in ready_path
    assert "cockpitMounted && cockpitFrame.src === url" in shell
    assert "}, 180);" in shell


def test_embedded_cockpit_avoids_duplicate_splash_and_heavy_offscreen_paint() -> None:
    entry = (ROOT / "frontend" / "web" / "src" / "main.tsx").read_text(
        encoding="utf-8"
    )
    styles = (ROOT / "frontend" / "web" / "src" / "index.css").read_text(
        encoding="utf-8"
    )

    assert "window.parent !== window" in entry
    assert "useState(!embeddedDesktop)" in entry
    assert "data-argus-embedded" in styles
    assert "content-visibility: auto" in styles
    assert "backdrop-filter: blur(8px)" in styles


def test_cockpit_theme_can_update_native_chrome_without_overlay_controls() -> None:
    host = (TAURI_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
    shell = (TAURI_ROOT / "src" / "main.ts").read_text(encoding="utf-8")
    cockpit = (ROOT / "frontend" / "web" / "src" / "useWorkbenchLayout.ts").read_text(encoding="utf-8")

    assert "apply_window_appearance" in host
    assert "set_window_theme" in host
    assert "set_large_preview" in host
    assert "window.maximize()" in host
    assert "window.unmaximize()" in host
    assert "argus:theme-changed" in shell
    assert "argus:large-preview" in shell
    assert "applyTheme(payload)" in shell
    assert "DwmSetWindowAttribute" in host
    assert "DWMWA_CAPTION_COLOR" in host
    assert "DWMWA_TEXT_COLOR" in host
    assert "argus:theme-changed" in cockpit


def test_delivery_notification_restores_the_authenticated_cockpit() -> None:
    main = (TAURI_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")

    assert "tauri_winrt_notification::Toast" in main
    assert '.title("Argus · 任务已完成")' in main
    assert ".text1(&input.title)" in main
    assert ".text2(body)" in main
    assert '"查看成果"' in main
    assert ".on_activated" in main
    assert '"argus:open-delivery"' in main
    assert "reveal_window(&click_app)" in main
    assert "if !backgrounded" not in main


def test_update_checks_leave_startup_idle_and_retry_transient_failures() -> None:
    updater = (TAURI_ROOT / "src-tauri" / "src" / "updater.rs").read_text(
        encoding="utf-8"
    )
    policy = (TAURI_ROOT / "src-tauri" / "src" / "update_policy.rs").read_text(
        encoding="utf-8"
    )

    assert "AUTO_START_DELAY: Duration = Duration::from_secs(30)" in updater
    assert "UPDATE_CHECK_TIMEOUT: Duration = Duration::from_secs(15)" in updater
    assert "PROGRESS_EMIT_INTERVAL" in updater
    smoke = (TAURI_ROOT / "scripts" / "smoke-host.py").read_text(encoding="utf-8")
    assert "automatic_check_due" in updater
    assert "automatic_retry_delay_seconds" in policy
    assert "ARGUS_DESKTOP_DISABLE_UPDATE_CHECK" in updater
    assert "ARGUS_DESKTOP_DISABLE_UPDATE_CHECK" in smoke


def test_release_build_requires_signed_tauri_update_artifacts() -> None:
    config = _config()
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    desktop_doc = (ROOT / "docs" / "windows-desktop.md").read_text(encoding="utf-8")

    assert config["bundle"]["createUpdaterArtifacts"] is True
    updater = config["plugins"]["updater"]
    assert updater["pubkey"]
    assert all(url.startswith("https://") for url in updater["endpoints"])
    assert "desktop-tauri" in workflow
    assert "TAURI_SIGNING_PRIVATE_KEY" in workflow
    assert "stage_webview2_loader" in (TAURI_ROOT / "src-tauri" / "build.rs").read_text(encoding="utf-8")
    stage = (TAURI_ROOT / "scripts" / "stage-release.ps1").read_text(encoding="utf-8")
    assert "Expected exactly one NSIS installer for version" in stage
    assert "Argus_${escapedVersion}" in stage
    assert (TAURI_ROOT / "scripts" / "smoke-host.py").is_file()
    assert "dangerousInsecureTransportProtocol" not in json.dumps(config)
    assert "也不允许证书绕过" in desktop_doc


def test_cockpit_settings_remain_project_scoped_while_cli_settings_are_in_file_menu() -> None:
    app = (ROOT / "frontend" / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    sidebar = (ROOT / "frontend" / "web" / "src" / "components" / "Sidebar.tsx").read_text(encoding="utf-8")
    shell = (TAURI_ROOT / "src" / "index.html").read_text(encoding="utf-8")
    shell_main = (TAURI_ROOT / "src" / "main.ts").read_text(encoding="utf-8")

    assert "onOpenPanel={(panel) => setOverlay(panel)}" in app
    assert "onOpenPanel('config')" in sidebar
    assert 'data-menu-action="settings"' in shell
    assert "await reopenWizard()" in shell_main
    assert "requestDesktopSetup" not in app


def test_trusted_shell_menu_merges_background_close_actions_and_matches_theme() -> None:
    shell = (TAURI_ROOT / "src" / "index.html").read_text(encoding="utf-8")
    styles = (TAURI_ROOT / "src" / "style.css").read_text(encoding="utf-8")
    host = (TAURI_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")

    assert shell.count("隐藏窗口并在后台继续") == 1
    assert "退出桌面界面（后台继续）" not in shell
    assert "quit-detached" not in host
    assert "install_menu" not in host
    assert "desktop-menu-bar" in styles
    assert "--chrome-bg: #f5f5f7" in styles
    assert "--chrome-bg: #161618" in styles
    assert "background: var(--chrome-bg)" in styles
    assert "border-bottom: 0" in styles
    assert "inset: var(--desktop-menu-height) 0 0" in styles


def test_native_brand_keeps_the_sclera_and_highlight_white_in_dark_mode() -> None:
    shell = (TAURI_ROOT / "src" / "index.html").read_text(encoding="utf-8")
    styles = (TAURI_ROOT / "src" / "style.css").read_text(encoding="utf-8")

    assert shell.count('class="argus-mark-eye-white"') == 3
    assert shell.count('class="argus-mark-pupil"') == 3
    assert shell.count('class="argus-mark-highlight"') == 3
    assert "--brand-body: #d7d9dc" in styles
    assert "--brand-eye: #ffffff" in styles
    assert "--brand-pupil: #202326" in styles
    assert "--brand-highlight: #ffffff" in styles
