use crate::models::{
    AppearanceTheme, DesktopAppearance, DesktopSettings, RunnerKind, RUNNER_KINDS,
};
use anyhow::Context;
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::RngCore;
use serde_json::Value;
use std::{
    collections::BTreeMap,
    env, fs,
    io::{self, Write},
    net::TcpListener,
    path::{Path, PathBuf},
    sync::Mutex,
};

/// Keep the established `argus-desktop` per-user root so Tauri releases retain
/// the Web token and can prove ownership of a prior bundled backend.
const CANONICAL_USER_DATA_DIR: &str = "argus-desktop";

const DESKTOP_PROFILE: &str = match option_env!("ARGUS_DESKTOP_PROFILE") {
    Some(profile) => profile,
    None => "argus",
};
const _: () = assert!(valid_desktop_profile(DESKTOP_PROFILE),
    "ARGUS_DESKTOP_PROFILE must be 1-32 lowercase ASCII letters/digits, starting with a letter");

const fn valid_desktop_profile(profile: &str) -> bool {
    let bytes = profile.as_bytes();
    if bytes.is_empty() || bytes.len() > 32 || !bytes[0].is_ascii_lowercase() {
        return false;
    }
    let mut index = 0;
    while index < bytes.len() {
        if !bytes[index].is_ascii_lowercase() && !bytes[index].is_ascii_digit() {
            return false;
        }
        index += 1;
    }
    true
}

pub(crate) fn desktop_profile() -> &'static str {
    DESKTOP_PROFILE
}

pub(crate) fn validate_application_identifier(identifier: &str) -> anyhow::Result<()> {
    validate_profile_identifier(desktop_profile(), identifier)
}

fn validate_profile_identifier(profile: &str, identifier: &str) -> anyhow::Result<()> {
    let argus_identifier = identifier == "cn.argusbot.desktop"
        || identifier.starts_with("cn.argusbot.desktop.");
    anyhow::ensure!((profile == "argus") == argus_identifier,
        "Desktop application identifier and compiled ARGUS_DESKTOP_PROFILE disagree; \
         compatible products must use their own identifier and storage profile.");
    Ok(())
}

pub struct SettingsStore {
    data_dir: PathBuf,
    settings_path: PathBuf,
    settings: Mutex<DesktopSettings>,
}

impl SettingsStore {
    pub fn open() -> anyhow::Result<Self> {
        let data_dir = desktop_data_dir();
        fs::create_dir_all(&data_dir)?;
        let settings_path = data_dir.join("settings.json");
        if desktop_profile() == "argus" && !crate::release::preview_mode() {
            migrate_legacy_settings(&data_dir, &settings_path)?;
        }
        let (settings, should_save) = load_settings_file(&settings_path)?;
        let store = Self {
            data_dir,
            settings_path,
            settings: Mutex::new(settings),
        };
        if should_save {
            store.save()?;
        }
        Ok(store)
    }

    pub fn data_dir(&self) -> &Path {
        &self.data_dir
    }

    pub fn snapshot(&self) -> DesktopSettings {
        self.settings
            .lock()
            .expect("settings mutex poisoned")
            .clone()
    }

    pub fn replace(&self, settings: DesktopSettings) -> anyhow::Result<()> {
        let mut current = self.settings.lock().expect("settings mutex poisoned");
        self.write_settings(&settings)?;
        *current = settings;
        Ok(())
    }

    pub fn set_appearance(&self, theme: AppearanceTheme) -> anyhow::Result<DesktopAppearance> {
        {
            let mut current = self.settings.lock().expect("settings mutex poisoned");
            if current.appearance_theme == theme { return Ok(self.appearance_from(&current)); }
            let mut next = current.clone();
            next.appearance_theme = theme;
            self.write_settings(&next)?;
            *current = next;
        }
        Ok(self.appearance())
    }

    pub fn appearance(&self) -> DesktopAppearance {
        self.appearance_from(&self.snapshot())
    }

    fn appearance_from(&self, settings: &DesktopSettings) -> DesktopAppearance {
        let theme = settings.appearance_theme.clone();
        DesktopAppearance {
            resolved_theme: match theme {
                AppearanceTheme::Dark => "dark".to_owned(),
                AppearanceTheme::Light | AppearanceTheme::System => "light".to_owned(),
            },
            theme,
        }
    }

    pub fn save(&self) -> anyhow::Result<()> {
        self.write_settings(&self.snapshot())
    }

    fn write_settings(&self, settings: &DesktopSettings) -> anyhow::Result<()> {
        let payload = serde_json::to_vec_pretty(settings)?;
        fs::create_dir_all(&self.data_dir)?;
        // Commit disk state before publishing it in memory. An interrupted write
        // must not destroy the saved CLI choice or local backend identity.
        let mut temporary = tempfile::NamedTempFile::new_in(&self.data_dir)?;
        temporary.write_all(&payload)?;
        temporary.as_file().sync_all()?;
        temporary.persist(&self.settings_path)?;
        Ok(())
    }

    pub fn api_base_url(settings: &DesktopSettings) -> String {
        format!("http://{}:{}", settings.host, settings.port)
    }

    pub fn is_artifact_download_url(settings: &DesktopSettings, url: &url::Url) -> bool {
        let backend = url::Url::parse(&Self::api_base_url(settings))
            .expect("desktop settings contain a valid backend URL");
        url.scheme() == "blob" && url.origin() == backend.origin()
    }

    pub fn cockpit_url(settings: &DesktopSettings) -> String {
        let token: String =
            url::form_urlencoded::byte_serialize(settings.token.as_bytes()).collect();
        let theme = match settings.appearance_theme {
            AppearanceTheme::System => "system", AppearanceTheme::Light => "light", AppearanceTheme::Dark => "dark",
        };
        format!("{}/?token={token}&desktopTheme={theme}", Self::api_base_url(settings))
    }
}

pub fn available_backend_port(settings: &DesktopSettings) -> io::Result<u16> {
    let listener = TcpListener::bind((settings.host.as_str(), settings.port))?;
    listener.local_addr().map(|address| address.port())
}

pub(crate) fn desktop_data_dir() -> PathBuf {
    // A new preview namespace gives manual acceptance a genuinely empty profile
    // without deleting or silently importing an earlier preview's account/data.
    let name = match (desktop_profile(), crate::release::preview_mode()) {
        ("argus", false) => CANONICAL_USER_DATA_DIR.to_owned(),
        ("argus", true) => "argus-desktop-preview-repair-20261101".to_owned(),
        (profile, false) => format!("{profile}-desktop"),
        (profile, true) => format!("{profile}-desktop-preview"),
    };
    #[cfg(not(windows))]
    {
        let home = env::var_os("HOME")
            .map(PathBuf::from)
            .expect("HOME is required for desktop settings");
        return home
            .join(if cfg!(target_os = "macos") {
                "Library/Application Support"
            } else {
                ".local/share"
            })
            .join(name);
    }
    #[cfg(windows)]
    {
        let app_data = env::var_os("APPDATA")
            .map(PathBuf::from)
            .or_else(|| {
                env::var_os("USERPROFILE")
                    .map(PathBuf::from)
                    .map(|home| home.join("AppData").join("Roaming"))
            })
            .unwrap_or_else(|| PathBuf::from("."));
        app_data.join(name)
    }
}

fn migrate_legacy_settings(data_dir: &Path, target: &Path) -> anyhow::Result<()> {
    if target.is_file() {
        return Ok(());
    }
    let Some(app_data) = data_dir.parent() else {
        return Ok(());
    };
    for name in ["Argus", "cn.argusbot.desktop"] {
        let candidate = app_data.join(name).join("settings.json");
        if candidate.is_file() {
            fs::copy(candidate, target)?;
            break;
        }
    }
    Ok(())
}

fn load_settings_file(path: &Path) -> anyhow::Result<(DesktopSettings, bool)> {
    let mut settings = DesktopSettings::default();
    let mut needs_save = false;
    let parsed = match fs::read_to_string(path) {
        Ok(raw) => Some(serde_json::from_str::<Value>(&raw)
            .context("桌面设置格式损坏；原文件已保留，请检查 settings.json。")?),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(error).context("无法读取桌面设置；原文件不会被覆盖。"),
    };

    if let Some(value) = parsed.as_ref() {
        anyhow::ensure!(value.is_object(), "桌面设置必须是 JSON 对象；原文件已保留。");
        settings = serde_json::from_value::<DesktopSettings>(value.clone())
            .context("桌面设置字段无效；原文件已保留。")?;
        let object = value.as_object();
        if object.and_then(|row| row.get("accentHue")).is_some()
            || object.and_then(|row| row.get("backendMode")).is_some()
        {
            needs_save = true;
        }
        if let Some(legacy) = object
            .and_then(|row| row.get("runnerBin"))
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
        {
            settings
                .runner_bins
                .entry("codex".to_owned())
                .or_insert_with(|| legacy.to_owned());
            if object.and_then(|row| row.get("runnerConfigured")).is_none() {
                settings.runner_configured = true;
            }
            needs_save = true;
        }
        if object.and_then(|row| row.get("runnerKind")).is_some()
            && object.and_then(|row| row.get("runnerConfigured")).is_none()
        {
            settings.runner_configured = true;
            needs_save = true;
        }
    }

    if settings.trial_mode {
        // Retire the old desktop-only profile without touching its account
        // files or projects. Offer the user's saved local CLI for confirmation.
        if let Some(own) = settings.own_account.take() {
            settings.runner_kind = own.runner_kind;
            settings.runner_bins.extend(own.runner_bins);
            settings.runner_configured = own.runner_configured;
        } else {
            settings.runner_configured = false;
        }
        settings.setup_complete = false;
        settings.trial_mode = false;
        needs_save = true;
    }
    if settings.own_account.take().is_some() {
        needs_save = true;
    }
    // Old releases only checked --version. Keep their CLI choice, but require
    // one real reply + action check before declaring the new protocol ready.
    if settings.setup_complete && settings.runner_validation_revision < 1 {
        settings.setup_complete = false;
        needs_save = true;
    }
    settings.host = settings.host.trim().to_owned();
    if settings.host != "127.0.0.1" {
        settings.host = "127.0.0.1".to_owned();
        needs_save = true;
    }
    if settings.port == 0 {
        settings.port = DesktopSettings::default().port;
        needs_save = true;
    }
    settings.runner_bins = normalized_runner_bins(&settings.runner_bins);
    if settings.token.trim().is_empty() {
        settings.token = random_token();
        needs_save = true;
    }
    Ok((settings, needs_save))
}

pub fn normalized_runner_bins(input: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    input
        .iter()
        .filter_map(|(kind, path)| {
            RUNNER_KINDS
                .contains(&kind.as_str())
                .then(|| (kind.trim(), path.trim()))
                .filter(|(_, path)| !path.is_empty())
                .map(|(kind, path)| (kind.to_owned(), path.to_owned()))
        })
        .collect()
}

pub fn random_token() -> String {
    let mut bytes = [0_u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    URL_SAFE_NO_PAD.encode(bytes)
}

pub fn runner_bin(settings: &DesktopSettings, kind: &RunnerKind) -> Option<String> {
    settings
        .runner_bins
        .get(kind.as_str())
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

#[cfg(test)]
mod tests {
    use super::{normalized_runner_bins, random_token, SettingsStore};
    use crate::models::DesktopSettings;
    use std::{
        collections::BTreeMap,
        io,
        net::TcpListener,
        sync::Mutex,
    };

    fn port_store(port: u16) -> (tempfile::TempDir, SettingsStore) {
        let directory = tempfile::tempdir_in(env!("CARGO_MANIFEST_DIR")).unwrap();
        let store = SettingsStore {
            data_dir: directory.path().to_path_buf(),
            settings_path: directory.path().join("settings.json"),
            settings: Mutex::new(DesktopSettings {
                port,
                token: "test-web-token".into(),
                runner_configured: true,
                setup_complete: true,
                ..Default::default()
            }),
        };
        store.save().unwrap();
        (directory, store)
    }

    #[test]
    fn artifact_downloads_accept_only_current_backend_blobs() {
        for port in [55418, 80] {
            let settings = DesktopSettings { port, ..Default::default() };
            let accepted = url::Url::parse(&format!("blob:http://127.0.0.1:{port}/artifact")).unwrap();
            assert!(SettingsStore::is_artifact_download_url(&settings, &accepted));
            for denied in [
                "blob:http://127.0.0.1:9999/artifact",
                "blob:https://example.com/artifact",
                "blob:null/artifact",
                "http://127.0.0.1:55418/api/projects",
                "file:///example.txt",
            ] {
                assert!(!SettingsStore::is_artifact_download_url(
                    &settings, &url::Url::parse(denied).unwrap()
                ));
            }
        }
    }

    #[test]
    fn manual_port_choices_are_never_reassigned() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let (_directory, store) = port_store(port);
        let original = std::fs::read(&store.settings_path).unwrap();
        assert_eq!(super::available_backend_port(&store.snapshot()).unwrap_err().kind(), io::ErrorKind::AddrInUse);
        assert_eq!(store.snapshot().port, port);
        assert_eq!(std::fs::read(&store.settings_path).unwrap(), original);
        drop(listener);
        assert_eq!(super::available_backend_port(&store.snapshot()).unwrap(), port);
    }

    #[test]
    fn save_failure_preserves_the_previous_selection() {
        let (_directory, mut store) = port_store(8799);
        let original = std::fs::read(&store.settings_path).unwrap();
        store.settings_path = store.data_dir.join("blocked");
        std::fs::create_dir(&store.settings_path).unwrap();
        let mut next = store.snapshot();
        next.port = 8800;
        assert!(store.replace(next).is_err());
        assert_eq!(store.snapshot().port, 8799);
        assert_eq!(std::fs::read(store.data_dir.join("settings.json")).unwrap(), original);
    }

    #[test]
    fn retired_trial_restores_local_selection_without_serializing_trial_state() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("settings.json");
        let original = r#"{"trialMode":true,"runnerKind":"copilot","runnerConfigured":true,
            "setupComplete":true,"token":"fixture-only","ownAccount":{"runnerKind":"pi",
            "runnerBins":{"pi":"C:/own/pi.cmd"},"runnerConfigured":true,"setupComplete":true}}"#;
        std::fs::write(&path, original).unwrap();
        let (migrated, save) = super::load_settings_file(&path).unwrap();
        assert!(save && migrated.runner_configured && !migrated.setup_complete);
        assert_eq!(migrated.runner_kind.as_str(), "pi");
        assert_eq!(migrated.runner_bins["pi"], "C:/own/pi.cmd");
        assert_eq!(migrated.token, "fixture-only");
        assert_eq!(std::fs::read_to_string(&path).unwrap(), original);
        let serialized = serde_json::to_value(&migrated).unwrap();
        assert!(serialized.get("trialMode").is_none() && serialized.get("ownAccount").is_none());
    }

    #[test]
    fn corrupt_settings_are_preserved_and_reported() {
        let directory = tempfile::tempdir().unwrap();
        let file = directory.path().join("settings.json");
        for raw in ["{broken", "[]", r#"{"port":"invalid"}"#] {
            std::fs::write(&file, raw).unwrap();
            assert!(super::load_settings_file(&file).is_err());
            assert_eq!(std::fs::read_to_string(&file).unwrap(), raw);
        }
    }

    #[test]
    fn compiled_product_storage_is_isolated() {
        let profile = option_env!("ARGUS_DESKTOP_PROFILE").unwrap_or("argus");
        let product_home = format!("{}_HOME", profile.to_ascii_uppercase());
        for explicit_home in ["default", "compat", "product", "both"] {
            let directory = tempfile::tempdir().unwrap();
            let mut command = std::process::Command::new(std::env::current_exe().unwrap());
            command.args(["--exact", "settings::tests::product_storage_child", "--ignored"])
                .env("ARGUS_STORAGE_TEST_ROOT", directory.path())
                .env("HOME", directory.path())
                .env("USERPROFILE", directory.path())
                .env("APPDATA", directory.path())
                .env_remove("ARGUS_SKILL_HOME")
                .env_remove(&product_home)
                // A launcher environment must not change a compiled product.
                .env("ARGUS_DESKTOP_PROFILE", "anotherproduct");
            if matches!(explicit_home, "compat" | "both") {
                command.env("ARGUS_SKILL_HOME", directory.path().join("explicit-engine"));
            }
            if matches!(explicit_home, "product" | "both") {
                command.env(&product_home, directory.path().join("product-engine"));
            }
            let output = command.output().unwrap();
            assert!(output.status.success(), "{}\n{}",
                String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        }
    }

    #[test]
    #[ignore = "isolated child process for compiled_product_storage_is_isolated"]
    fn product_storage_child() {
        let root = std::path::PathBuf::from(std::env::var_os("ARGUS_STORAGE_TEST_ROOT").unwrap());
        let base = if cfg!(windows) { root.clone() }
            else if cfg!(target_os = "macos") { root.join("Library/Application Support") }
            else { root.join(".local/share") };
        let profile = option_env!("ARGUS_DESKTOP_PROFILE").unwrap_or("argus");
        let preview = crate::release::preview_mode();
        let name = match (profile, preview) {
            ("argus", false) => "argus-desktop".to_owned(),
            ("argus", true) => "argus-desktop-preview-repair-20261101".to_owned(),
            (_, false) => format!("{profile}-desktop"),
            (_, true) => format!("{profile}-desktop-preview"),
        };
        let legacy = base.join("Argus").join("settings.json");
        std::fs::create_dir_all(legacy.parent().unwrap()).unwrap();
        let legacy_bytes = br#"{"token":"legacy-token","runnerConfigured":true,"runnerKind":"copilot"}"#;
        std::fs::write(&legacy, legacy_bytes).unwrap();
        let ownership = base.join("argus-desktop/runtime/backend.json");
        std::fs::create_dir_all(ownership.parent().unwrap()).unwrap();
        std::fs::write(&ownership, b"existing ownership must not move").unwrap();
        let store = super::SettingsStore::open().unwrap();
        assert_eq!(store.data_dir(), base.join(name));
        if profile == "argus" && !preview {
            assert_eq!(store.snapshot().token, "legacy-token");
        } else {
            assert_ne!(store.snapshot().token, "legacy-token");
            assert!(!store.snapshot().runner_configured);
            assert!(!store.data_dir().join("runtime/backend.json").exists());
        }
        let expected_home = if preview { store.data_dir().join("argus-home") }
            else if let Some(explicit) = std::env::var_os("ARGUS_SKILL_HOME") {
                std::path::PathBuf::from(explicit)
            } else if profile == "argus" { root.join(".argus-skill") }
            else if let Some(explicit) = std::env::var_os(format!("{}_HOME", profile.to_ascii_uppercase())) {
                std::path::PathBuf::from(explicit)
            }
            else { root.join(format!(".{profile}")) };
        assert_eq!(crate::runner::argus_home_dir(), expected_home);
        assert_eq!(std::fs::read(&legacy).unwrap(), legacy_bytes);
        assert_eq!(std::fs::read(&ownership).unwrap(), b"existing ownership must not move");
        let token = store.snapshot().token;
        assert_eq!(super::SettingsStore::open().unwrap().snapshot().token, token);
    }

    #[test]
    fn product_profiles_cannot_select_paths_or_argus_engine_home() {
        for profile in ["argus", "vulcan", "product2"] {
            assert!(super::valid_desktop_profile(profile));
        }
        for profile in ["", "../argus", "argus-skill", ".argus-skill", "/tmp", "Vulcan", "2app",
                        "a\\b", " app", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"] {
            assert!(!super::valid_desktop_profile(profile), "{profile}");
        }
    }

    #[test]
    fn compatible_identifiers_require_a_separate_compiled_storage_profile() {
        for identifier in ["cn.argusbot.desktop", "cn.argusbot.desktop.preview.integration20260913"] {
            assert!(super::validate_profile_identifier("argus", identifier).is_ok());
            assert!(super::validate_profile_identifier("vulcan", identifier).is_err());
        }
        assert!(super::validate_profile_identifier("argus", "io.github.ahappend.vulcan").is_err());
        assert!(super::validate_profile_identifier("vulcan", "io.github.ahappend.vulcan").is_ok());
    }

    #[test]
    fn failed_save_does_not_publish_unsaved_settings() {
        let directory = tempfile::tempdir().unwrap();
        let store = super::SettingsStore {
            data_dir: directory.path().to_path_buf(),
            settings_path: directory.path().join("blocked"),
            settings: std::sync::Mutex::new(Default::default()),
        };
        std::fs::create_dir(&store.settings_path).unwrap();
        let mut changed = store.snapshot();
        changed.port = 19876;
        assert!(store.replace(changed).is_err());
        assert_ne!(store.snapshot().port, 19876);
    }

    #[test]
    fn a_new_profile_defaults_to_light_without_following_the_os_theme() {
        let directory = tempfile::tempdir().unwrap();
        let file = directory.path().join("appearance-fixture.json");
        let (settings, _) = super::load_settings_file(&file).unwrap();
        assert_eq!(settings.appearance_theme, crate::models::AppearanceTheme::Light);
        assert!(!file.exists());
    }

    #[test]
    fn an_existing_explicit_theme_is_not_reset_to_the_new_default() {
        let directory = tempfile::tempdir().unwrap();
        let file = directory.path().join("appearance-fixture.json");
        for (name, theme) in [
            ("dark", crate::models::AppearanceTheme::Dark),
            ("system", crate::models::AppearanceTheme::System),
            ("light", crate::models::AppearanceTheme::Light),
        ] {
            let raw = format!(r#"{{"appearanceTheme":"{name}"}}"#);
            std::fs::write(&file, &raw).unwrap();
            let (settings, _) = super::load_settings_file(&file).unwrap();
            assert_eq!(settings.appearance_theme, theme);
            assert_eq!(std::fs::read_to_string(&file).unwrap(), raw);
        }
    }

    #[test]
    fn legacy_eye_choices_are_ignored_without_resetting_saved_theme_or_runner() {
        let directory = tempfile::tempdir().unwrap();
        let file = directory.path().join("appearance-fixture.json");
        for choice in ["off", "system", "on"] {
            let original = format!(r#"{{"appearanceTheme":"dark","runnerKind":"pi","runnerConfigured":true,"setupComplete":true,"runnerValidationRevision":1,"startupEyeMotion":"{choice}"}}"#);
            std::fs::write(&file, &original).unwrap();
            let (settings, _) = super::load_settings_file(&file).unwrap();
            assert_eq!(settings.appearance_theme, crate::models::AppearanceTheme::Dark);
            assert_eq!(settings.runner_kind, crate::models::RunnerKind::Pi);
            assert!(settings.runner_configured && settings.setup_complete);
            assert!(serde_json::to_value(&settings).unwrap().get("startupEyeMotion").is_none());
            assert_eq!(std::fs::read_to_string(&file).unwrap(), original);
        }
    }

    #[test]
    fn token_is_url_safe_and_nonempty() {
        let token = random_token();
        assert!(token.len() >= 40);
        assert!(token
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_'));
    }

    #[test]
    fn runner_bins_ignore_unknown_and_empty_values() {
        let source = BTreeMap::from([
            ("codex".to_owned(), " C:/bin/codex.cmd ".to_owned()),
            ("unknown".to_owned(), "C:/bad".to_owned()),
            ("pi".to_owned(), " ".to_owned()),
        ]);
        assert_eq!(
            normalized_runner_bins(&source),
            BTreeMap::from([("codex".to_owned(), "C:/bin/codex.cmd".to_owned())]),
        );
    }

    #[test]
    fn unfinished_onboarding_is_not_marked_configured() {
        let directory = tempfile::tempdir().unwrap();
        let file = directory.path().join("settings.json");
        std::fs::write(&file, r#"{"runnerConfigured":false,"setupComplete":false}"#).unwrap();
        let (settings, needs_save) = super::load_settings_file(&file).unwrap();
        assert!(needs_save);
        assert!(!settings.runner_configured);
        assert!(!settings.setup_complete);
    }

    #[test]
    fn missing_settings_require_a_real_backend_selection() {
        let directory = tempfile::tempdir().unwrap();
        let (settings, needs_save) =
            super::load_settings_file(&directory.path().join("settings.json")).unwrap();
        assert!(needs_save);
        assert!(!settings.runner_configured);
        assert!(!settings.setup_complete);
        assert!(!settings.token.is_empty());
    }

    #[test]
    fn explicit_saved_desktop_choice_is_preserved() {
        let directory = tempfile::tempdir().unwrap();
        let file = directory.path().join("settings.json");
        std::fs::write(
            &file,
            r#"{"runnerKind":"copilot","runnerConfigured":true,"setupComplete":true,"runnerValidationRevision":1}"#,
        )
        .unwrap();
        let (settings, _) = super::load_settings_file(&file).unwrap();
        assert_eq!(settings.runner_kind, crate::models::RunnerKind::Copilot);
        assert!(settings.runner_configured);
        assert!(settings.setup_complete);
    }

    #[test]
    fn legacy_version_only_readiness_requires_real_revalidation_without_losing_choice() {
        let directory = tempfile::tempdir().unwrap();
        let file = directory.path().join("settings.json");
        let raw = r#"{"runnerKind":"pi","runnerBins":{"pi":"C:/own/pi.cmd"},"runnerConfigured":true,"setupComplete":true}"#;
        std::fs::write(&file, raw).unwrap();
        let (settings, save) = super::load_settings_file(&file).unwrap();
        assert!(save && settings.runner_configured && !settings.setup_complete);
        assert_eq!(settings.runner_kind, crate::models::RunnerKind::Pi);
        assert_eq!(settings.runner_bins["pi"], "C:/own/pi.cmd");
        assert_eq!(std::fs::read_to_string(&file).unwrap(), raw);
    }

    #[test]
    fn legacy_explicit_runner_path_remains_a_desktop_choice() {
        let directory = tempfile::tempdir().unwrap();
        let file = directory.path().join("settings.json");
        std::fs::write(&file, r#"{"runnerBin":"C:/agents/codex.cmd"}"#).unwrap();
        let (settings, _) = super::load_settings_file(&file).unwrap();
        assert!(settings.runner_configured);
        assert_eq!(settings.runner_bins["codex"], "C:/agents/codex.cmd");
    }
}
