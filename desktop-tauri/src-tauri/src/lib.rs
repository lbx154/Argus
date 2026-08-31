mod backend;
mod diagnostics;
mod identity;
mod logger;
mod models;
mod process;
mod redaction;
mod release;
mod resilience;
mod runner;
mod settings;
mod update_policy;
mod updater;

use backend::BackendSupervisor;
use diagnostics::export_diagnostics as write_diagnostics;
use logger::DesktopLogger;
use models::{
    AppearanceTheme, BackendState, CompleteSetupInput, DeliveryNotificationInput,
    DesktopAppearance, DesktopSetup, SetupResult, UpdateStatus,
};
use release::{development_mode, repo_root, runtime_identity, ReleaseContext};
use runner::detect_pi_configuration;
use settings::{normalized_runner_bins, SettingsStore};
use std::{
    collections::{HashSet, VecDeque},
    fs,
    path::Path,
    process::Command,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::Duration,
};
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent},
    window::Color,
    AppHandle, Emitter, Manager, RunEvent, State, Theme, WindowEvent,
};
#[cfg(not(windows))]
use tauri_plugin_notification::NotificationExt;
use updater::UpdateManager;

const MAIN_WINDOW: &str = "main";

pub struct AppState {
    settings: Arc<SettingsStore>,
    supervisor: Arc<BackendSupervisor>,
    updater: Arc<UpdateManager>,
    logger: DesktopLogger,
    quitting: AtomicBool,
    delivered: Mutex<DeliveryDedupe>,
    preview_restore_maximized: Mutex<Option<bool>>,
    tray: Mutex<Option<TrayIcon>>,
}

struct DeliveryDedupe {
    ids: HashSet<String>,
    order: VecDeque<String>,
}

impl DeliveryDedupe {
    fn remember(&mut self, id: String) -> bool {
        if !self.ids.insert(id.clone()) {
            return false;
        }
        self.order.push_back(id);
        while self.order.len() > 100 {
            if let Some(expired) = self.order.pop_front() {
                self.ids.remove(&expired);
            }
        }
        true
    }
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct AppearanceInput {
    theme: AppearanceTheme,
}

fn state(app: &AppHandle) -> State<'_, AppState> {
    app.state::<AppState>()
}

fn reveal_window(app: &AppHandle) {
    let Some(window) = app.get_webview_window(MAIN_WINDOW) else {
        return;
    };
    if window.is_minimized().unwrap_or(false) {
        let _ = window.unminimize();
    }
    let _ = window.show();
    let _ = window.set_focus();
}

fn hide_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(MAIN_WINDOW) {
        let _ = window.hide();
    }
}

#[cfg(windows)]
fn apply_windows_caption_palette(window: &tauri::WebviewWindow, theme: &AppearanceTheme) {
    use std::{ffi::c_void, mem::size_of};
    use windows_sys::Win32::Graphics::Dwm::{
        DwmSetWindowAttribute, DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_COLOR_DEFAULT,
        DWMWA_TEXT_COLOR, DWMWA_USE_IMMERSIVE_DARK_MODE,
    };

    // COLORREF stores red in the low byte (0x00bbggrr).
    const fn colorref(red: u32, green: u32, blue: u32) -> u32 {
        red | (green << 8) | (blue << 16)
    }

    let (caption, text, border, immersive_dark) = match theme {
        AppearanceTheme::Light => (
            colorref(234, 242, 255),
            colorref(17, 24, 39),
            colorref(234, 242, 255),
            0_i32,
        ),
        AppearanceTheme::Dark => (
            colorref(17, 29, 48),
            colorref(249, 250, 251),
            colorref(17, 29, 48),
            1_i32,
        ),
        AppearanceTheme::System => (
            DWMWA_COLOR_DEFAULT,
            DWMWA_COLOR_DEFAULT,
            DWMWA_COLOR_DEFAULT,
            0_i32,
        ),
    };
    let Ok(hwnd) = window.hwnd() else {
        return;
    };
    let hwnd = hwnd.0 as windows_sys::Win32::Foundation::HWND;
    // These Windows 11 attributes fail harmlessly on older DWM versions. Native
    // caption buttons remain system-owned; only their palette is synchronized.
    unsafe {
        for (attribute, value) in [
            (DWMWA_CAPTION_COLOR, caption),
            (DWMWA_TEXT_COLOR, text),
            (DWMWA_BORDER_COLOR, border),
        ] {
            let _ = DwmSetWindowAttribute(
                hwnd,
                attribute as u32,
                (&value as *const u32).cast::<c_void>(),
                size_of::<u32>() as u32,
            );
        }
        let _ = DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE as u32,
            (&immersive_dark as *const i32).cast::<c_void>(),
            size_of::<i32>() as u32,
        );
    }
}

/// Keep Windows non-client chrome visually aligned with the currently visible
/// launcher/cockpit surface without replacing native caption controls.
fn apply_window_appearance(app: &AppHandle, theme: &AppearanceTheme) {
    let (native_theme, background) = match theme {
        AppearanceTheme::Light => (Some(Theme::Light), Some(Color(249, 250, 251, 255))),
        AppearanceTheme::Dark => (Some(Theme::Dark), Some(Color(13, 14, 18, 255))),
        AppearanceTheme::System => (None, None),
    };
    if let Some(window) = app.get_webview_window(MAIN_WINDOW) {
        let _ = window.set_theme(native_theme);
        let _ = window.set_background_color(background);
        #[cfg(windows)]
        apply_windows_caption_palette(&window, theme);
    }
}

fn open_directory(path: &Path) -> Result<String, String> {
    fs::create_dir_all(path).map_err(|error| error.to_string())?;
    #[cfg(windows)]
    Command::new("explorer.exe")
        .arg(path)
        .spawn()
        .map_err(|error| error.to_string())?;
    #[cfg(target_os = "macos")]
    Command::new("open")
        .arg(path)
        .spawn()
        .map_err(|error| error.to_string())?;
    #[cfg(all(unix, not(target_os = "macos")))]
    Command::new("xdg-open")
        .arg(path)
        .spawn()
        .map_err(|error| error.to_string())?;
    Ok(path.to_string_lossy().into_owned())
}

fn request_stop_and_quit(app: &AppHandle) {
    let app_handle = app.clone();
    let app_state = state(app);
    app_state.quitting.store(true, Ordering::SeqCst);
    let supervisor = Arc::clone(&app_state.supervisor);
    tauri::async_runtime::spawn(async move {
        supervisor.stop().await;
        app_handle.exit(0);
    });
}

fn handle_menu(app: &AppHandle, id: &str) {
    match id {
        "show" => reveal_window(app),
        "hide" => hide_window(app),
        "stop-quit" => request_stop_and_quit(app),
        _ => {}
    }
}

fn install_tray(app: &AppHandle) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "显示 Argus", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, "hide", "隐藏窗口", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let stop = MenuItem::with_id(app, "stop-quit", "停止本地后端并退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &hide, &separator, &stop])?;
    let icon = app.default_window_icon().cloned();
    let mut builder = TrayIconBuilder::with_id("argus-tray")
        .tooltip("Argus · 后台运行")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| handle_menu(app, event.id().as_ref()))
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            }
            | TrayIconEvent::DoubleClick {
                button: MouseButton::Left,
                ..
            } = event
            {
                reveal_window(tray.app_handle());
            }
        });
    if let Some(icon) = icon {
        builder = builder.icon(icon);
    }
    let tray = builder.build(app)?;
    *state(app).tray.lock().expect("tray mutex poisoned") = Some(tray);
    Ok(())
}

fn make_release_context(app: &AppHandle) -> ReleaseContext {
    ReleaseContext {
        development: development_mode(),
        app_version: app.package_info().version.to_string(),
        repo_root: repo_root(),
        resource_dir: app.path().resource_dir().ok(),
    }
}

fn initialize(app: &AppHandle) -> tauri::Result<()> {
    let settings = Arc::new(SettingsStore::open().map_err(tauri::Error::Anyhow)?);
    let logger = DesktopLogger::new(settings.data_dir()).map_err(tauri::Error::Anyhow)?;
    let release = make_release_context(app);
    let supervisor = BackendSupervisor::new(
        app.clone(),
        Arc::clone(&settings),
        release.clone(),
        logger.clone(),
    )
    .map_err(tauri::Error::Anyhow)?;
    let updater = UpdateManager::new(
        app.clone(),
        settings.data_dir().to_path_buf(),
        &release,
        logger.clone(),
    );
    let initial_theme = settings.snapshot().appearance_theme;
    app.manage(AppState {
        settings,
        supervisor: Arc::clone(&supervisor),
        updater: Arc::clone(&updater),
        logger,
        quitting: AtomicBool::new(false),
        delivered: Mutex::new(DeliveryDedupe {
            ids: HashSet::new(),
            order: VecDeque::new(),
        }),
        preview_restore_maximized: Mutex::new(None),
        tray: Mutex::new(None),
    });
    // Keep the native non-client frame, but render the small File/Help strip in
    // the trusted local shell so its gradient follows the cockpit theme. The
    // system tray remains native.
    install_tray(app)?;
    apply_window_appearance(app, &initial_theme);
    reveal_window(app);
    tauri::async_runtime::spawn(async move {
        supervisor.start().await;
    });
    updater.spawn_automatic_check();
    Ok(())
}

#[tauri::command]
fn get_status(app: AppHandle) -> models::BackendStatus {
    state(&app).supervisor.current_status()
}

#[tauri::command]
fn get_setup(app: AppHandle) -> DesktopSetup {
    let app_state = state(&app);
    let settings = app_state.settings.snapshot();
    let status = app_state.supervisor.current_status();
    DesktopSetup {
        complete: settings.setup_complete,
        host: settings.host,
        port: settings.port,
        runner_kind: settings.runner_kind,
        runner_bins: settings.runner_bins,
        runner_configured: settings.runner_configured,
        detected_runners: app_state.supervisor.detected_runners(),
        pi_configuration: detect_pi_configuration(),
        release_identity: app_state.supervisor.release().identity(),
        runtime_identity: runtime_identity(&status),
    }
}

#[tauri::command]
fn get_appearance(app: AppHandle) -> DesktopAppearance {
    state(&app).settings.appearance()
}

#[tauri::command]
fn set_appearance(app: AppHandle, input: AppearanceInput) -> Result<DesktopAppearance, String> {
    let theme = input.theme.clone();
    let appearance = state(&app)
        .settings
        .set_appearance(input.theme)
        .map_err(|error| error.to_string())?;
    apply_window_appearance(&app, &theme);
    Ok(appearance)
}

#[tauri::command]
fn set_window_theme(app: AppHandle, theme: AppearanceTheme) {
    apply_window_appearance(&app, &theme);
}

#[tauri::command]
fn set_large_preview(app: AppHandle, active: bool) {
    let Some(window) = app.get_webview_window(MAIN_WINDOW) else {
        return;
    };
    let app_state = state(&app);
    let mut restore = app_state
        .preview_restore_maximized
        .lock()
        .expect("preview state mutex poisoned");
    if active {
        if restore.is_some() {
            return;
        }
        let was_maximized = window.is_maximized().unwrap_or(false);
        *restore = Some(was_maximized);
        if !was_maximized {
            let _ = window.maximize();
        }
    } else if matches!(restore.take(), Some(false)) {
        let _ = window.unmaximize();
    }
}

#[tauri::command]
async fn choose_runner(kind: models::RunnerKind) -> Result<Option<String>, String> {
    let label = kind.label();
    Ok(rfd::AsyncFileDialog::new()
        .set_title(format!("选择 {label}"))
        .add_filter(label, &["cmd", "exe"])
        .add_filter("所有文件", &["*"])
        .pick_file()
        .await
        .map(|file| file.path().to_string_lossy().into_owned()))
}

#[tauri::command]
async fn complete_setup(app: AppHandle, input: CompleteSetupInput) -> SetupResult {
    if !(1024..=65535).contains(&input.port) {
        return SetupResult::error("端口需在 1024 - 65535 之间");
    }
    let app_state = state(&app);
    let previous = app_state.settings.snapshot();
    let mut next = previous.clone();
    next.port = input.port as u16;
    next.runner_kind = input.runner_kind;
    next.runner_bins = normalized_runner_bins(&input.runner_bins);
    next.runner_configured = true;
    next.setup_complete = true;
    let runtime_changed = previous.port != next.port
        || previous.runner_kind != next.runner_kind
        || previous.runner_configured != next.runner_configured
        || previous.runner_bins != next.runner_bins;
    if let Err(error) = app_state.settings.replace(next) {
        return SetupResult::error(error.to_string());
    }
    if !runtime_changed {
        return SetupResult::ok();
    }
    let supervisor = Arc::clone(&app_state.supervisor);
    supervisor.restart().await;
    let deadline = tokio::time::Instant::now() + Duration::from_secs(30);
    loop {
        let status = supervisor.current_status();
        if status.state == BackendState::Ready {
            return SetupResult::ok();
        }
        if status.state == BackendState::Error || tokio::time::Instant::now() >= deadline {
            return SetupResult::error(status.detail.unwrap_or(status.message));
        }
        tokio::time::sleep(Duration::from_millis(150)).await;
    }
}

#[tauri::command]
async fn restart_backend(app: AppHandle) -> bool {
    state(&app).supervisor.restart().await;
    true
}

#[tauri::command]
async fn export_diagnostics(app: AppHandle) -> Result<Option<String>, String> {
    let app_state = state(&app);
    write_diagnostics(
        app_state.settings.data_dir().to_path_buf(),
        app.package_info().version.to_string(),
        app_state.logger.clone(),
    )
    .await
}

#[tauri::command]
fn hide_desktop(app: AppHandle) {
    hide_window(&app);
}

#[tauri::command]
fn stop_backend_and_quit(app: AppHandle) {
    request_stop_and_quit(&app);
}

#[tauri::command]
fn show_about(app: AppHandle) {
    let version = app.package_info().version.to_string();
    let _ = rfd::MessageDialog::new()
        .set_title("关于 Argus")
        .set_description(format!(
            "Argus {version}\nTauri / Rust\nWindows desktop host"
        ))
        .set_buttons(rfd::MessageButtons::Ok)
        .show();
}

#[tauri::command]
fn open_logs(app: AppHandle) -> Result<String, String> {
    open_directory(&state(&app).settings.data_dir().join("logs"))
}

#[tauri::command]
fn open_data(app: AppHandle) -> Result<String, String> {
    open_directory(state(&app).settings.data_dir())
}

#[tauri::command]
fn open_cockpit(app: AppHandle) -> Result<String, String> {
    let app_state = state(&app);
    let settings = app_state.settings.snapshot();
    let status = app_state.supervisor.current_status();
    if status.state != BackendState::Ready {
        return Err("Argus 本地服务尚未就绪。".to_owned());
    }
    app_state
        .logger
        .info("authenticated cockpit URL issued to the local shell");
    Ok(SettingsStore::cockpit_url(&settings))
}

#[tauri::command]
fn open_external(url: String) -> Result<(), String> {
    let parsed = url::Url::parse(&url).map_err(|_| "链接格式无效。".to_owned())?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err("只允许在系统浏览器中打开 HTTP(S) 链接。".to_owned());
    }
    #[cfg(windows)]
    Command::new("explorer.exe")
        .arg(parsed.as_str())
        .spawn()
        .map_err(|error| error.to_string())?;
    #[cfg(target_os = "macos")]
    Command::new("open")
        .arg(parsed.as_str())
        .spawn()
        .map_err(|error| error.to_string())?;
    #[cfg(all(unix, not(target_os = "macos")))]
    Command::new("xdg-open")
        .arg(parsed.as_str())
        .spawn()
        .map_err(|error| error.to_string())?;
    Ok(())
}

#[cfg(windows)]
fn show_delivery_notification(
    app: &AppHandle,
    input: &DeliveryNotificationInput,
    body: &str,
) -> bool {
    // Use the native Windows toast pattern: a short state title, one task line,
    // one concise result line, and a single clear action. Clicking either the
    // body or action restores Argus and opens the result when one exists.
    use tauri_winrt_notification::Toast;

    let click_app = app.clone();
    let click_payload = input.clone();
    let action_label = if input.path.is_some() {
        "查看成果"
    } else {
        "查看任务"
    };
    Toast::new(app.config().identifier.as_str())
        .title("Argus · 任务已完成")
        .text1(&input.title)
        .text2(body)
        .add_button(action_label, "open-delivery")
        .on_activated(move |_action| {
            reveal_window(&click_app);
            let _ = click_app.emit("argus:open-delivery", click_payload.clone());
            Ok(())
        })
        .show()
        .is_ok()
}

#[cfg(not(windows))]
fn show_delivery_notification(
    app: &AppHandle,
    input: &DeliveryNotificationInput,
    body: &str,
) -> bool {
    app.notification()
        .builder()
        .title("Argus · Task complete")
        .body(format!("{}\n{}", input.title, body))
        .show()
        .is_ok()
}

#[tauri::command]
fn notify_delivery(app: AppHandle, input: DeliveryNotificationInput) -> bool {
    let Some(input) = input.bounded() else {
        return false;
    };
    let app_state = state(&app);
    if !app_state
        .delivered
        .lock()
        .expect("delivery mutex poisoned")
        .remember(input.delivery_id.clone())
    {
        return false;
    }
    let body = if input.summary.is_empty() {
        "任务已完成，可返回 Argus 查看结果。".to_owned()
    } else {
        input.summary.clone()
    };
    show_delivery_notification(&app, &input, &body)
}

#[tauri::command]
fn get_update_status(app: AppHandle) -> UpdateStatus {
    state(&app).updater.status()
}

#[tauri::command]
async fn check_for_update(app: AppHandle, manual: bool) -> UpdateStatus {
    state(&app).updater.check(manual).await
}

#[tauri::command]
async fn install_update(app: AppHandle) -> Result<(), String> {
    state(&app).updater.install().await
}

#[tauri::command]
fn dismiss_update(app: AppHandle) {
    state(&app).updater.dismiss();
}

pub fn run() {
    // Test-only escape hatch: the packaged smoke test owns an isolated AppData
    // directory but must not interfere with a real operator's single instance.
    // Production always remains single-instance.
    let builder = if std::env::var("ARGUS_DESKTOP_DISABLE_SINGLE_INSTANCE").as_deref() == Ok("1") {
        tauri::Builder::default()
    } else {
        tauri::Builder::default().plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            reveal_window(app)
        }))
    }
    .plugin(tauri_plugin_notification::init())
    .plugin(tauri_plugin_updater::Builder::new().build())
    .setup(|app| Ok(initialize(app.handle())?))
    .on_menu_event(|app, event| handle_menu(app, event.id().as_ref()))
    .on_window_event(|window, event| {
        if let WindowEvent::CloseRequested { api, .. } = event {
            let app_state = window.state::<AppState>();
            if !app_state.quitting.load(Ordering::SeqCst) {
                api.prevent_close();
                let _ = window.hide();
                app_state.logger.info(
                    "desktop window hidden; owned backend remains available in the background",
                );
            }
        }
    })
    .invoke_handler(tauri::generate_handler![
        get_status,
        get_setup,
        get_appearance,
        set_appearance,
        set_window_theme,
        set_large_preview,
        choose_runner,
        complete_setup,
        restart_backend,
        export_diagnostics,
        hide_desktop,
        stop_backend_and_quit,
        show_about,
        open_logs,
        open_data,
        open_cockpit,
        open_external,
        notify_delivery,
        get_update_status,
        check_for_update,
        install_update,
        dismiss_update,
    ]);
    let app = builder
        .build(tauri::generate_context!())
        .expect("error while building Argus Tauri application");
    app.run(|app, event| {
        if let RunEvent::ExitRequested { .. } = event {
            app.state::<AppState>()
                .quitting
                .store(true, Ordering::SeqCst);
        }
    });
}
