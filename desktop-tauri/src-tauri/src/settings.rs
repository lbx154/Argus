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
    io::Write,
    path::{Path, PathBuf},
    sync::Mutex,
};

/// Keep the established `argus-desktop` per-user root so Tauri releases retain
/// the Web token and the operator's choices.
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
        // must not destroy the saved CLI choice or the Web token.
        let mut temporary = tempfile::NamedTempFile::new_in(&self.data_dir)?;
        temporary.write_all(&payload)?;
        temporary.as_file().sync_all()?;
        temporary.persist(&self.settings_path)?;
        Ok(())
    }

    /// The cockpit at `origin`, where Argus listens, signed in with the token.
    pub fn cockpit_url(origin: &str, settings: &DesktopSettings) -> String {
        let token: String =
            url::form_urlencoded::byte_serialize(settings.token.as_bytes()).collect();
        let theme = match settings.appearance_theme {
            AppearanceTheme::System => "system", AppearanceTheme::Light => "light", AppearanceTheme::Dark => "dark",
        };
        format!("{origin}/?token={token}&desktopTheme={theme}")
    }
}

pub(crate) fn desktop_data_dir() -> PathBuf {
    // A new preview namespace gives manual acceptance a genuinely empty profile
    // without deleting or silently importing an earlier preview's account/data.
    let name = match (desktop_profile(), crate::release::preview_mode()) {
        ("argus", false) => CANONICAL_USER_DATA_DIR.to_owned(),
        ("argus", true) => "argus-desktop-preview-1.0.0".to_owned(),
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

    if let Some(mut value) = parsed {
        anyhow::ensure!(value.is_object(), "桌面设置必须是 JSON 对象；原文件已保留。");
        // A CLI Argus no longer drives is chosen again at setup.
        let retired = value.get("runnerKind").and_then(Value::as_str)
            .is_some_and(|kind| !RUNNER_KINDS.iter().any(|known| known.as_str() == kind));
        if retired {
            for key in ["runnerKind", "runnerConfigured", "setupComplete"] {
                value.as_object_mut().expect("checked above").remove(key);
            }
            needs_save = true;
        }
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
                .iter()
                .any(|known| known.as_str() == kind)
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
    use super::{normalized_runner_bins, random_token};
    use crate::models::{DesktopSettings, RunnerKind};
    use std::collections::BTreeMap;

    #[test]
    fn the_cockpit_url_signs_in_at_the_origin_argus_reports() {
        let settings = DesktopSettings { token: "a b&c".into(), ..Default::default() };
        let url = url::Url::parse(&super::SettingsStore::cockpit_url("http://127.0.0.1:41234", &settings)).unwrap();
        assert_eq!(url.port(), Some(41234));
        assert!(url.query_pairs().any(|(key, value)| key == "token" && value == "a b&c"));
        assert!(url.query_pairs().any(|(key, value)| key == "desktopTheme" && value == "light"));
    }

    #[test]
    fn a_retired_cli_is_chosen_again_without_losing_other_settings() {
        let directory = tempfile::tempdir().unwrap();
        let file = directory.path().join("settings.json");
        let raw = r#"{"token":"kept","port":9001,"runnerKind":"unknown-retired-cli","runnerConfigured":true,"setupComplete":true,"trialMode":false,"host":"127.0.0.1"}"#;
        std::fs::write(&file, raw).unwrap();
        let (settings, needs_save) = super::load_settings_file(&file).unwrap();
        assert!(needs_save);
        assert_eq!((settings.token.as_str(), settings.port), ("kept", 9001));
        assert_eq!(settings.runner_kind, RunnerKind::Codex);
        assert!(!settings.runner_configured && !settings.setup_complete);
    }

    #[test]
    fn all_legacy_cli_selections_survive_the_typescript_upgrade() {
        let directory = tempfile::tempdir().unwrap();
        let file = directory.path().join("settings.json");
        for kind in crate::models::RUNNER_KINDS.iter() {
            let raw = format!(r#"{{"runnerKind":"{}","runnerConfigured":true,"setupComplete":true}}"#, kind.as_str());
            std::fs::write(&file, raw).unwrap();
            let (settings, _) = super::load_settings_file(&file).unwrap();
            assert_eq!(settings.runner_kind, *kind);
            assert!(settings.runner_configured && settings.setup_complete);
        }
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
        for explicit_home in ["default", "argus", "product"] {
            let directory = tempfile::tempdir().unwrap();
            let mut command = std::process::Command::new(std::env::current_exe().unwrap());
            command.args(["--exact", "settings::tests::product_storage_child", "--ignored"])
                .env("ARGUS_STORAGE_TEST_ROOT", directory.path())
                .env("HOME", directory.path())
                .env("USERPROFILE", directory.path())
                .env("APPDATA", directory.path())
                .env_remove("ARGUS_HOME")
                .env_remove(&product_home)
                // A launcher environment must not change a compiled product.
                .env("ARGUS_DESKTOP_PROFILE", "anotherproduct");
            if explicit_home == "argus" {
                command.env("ARGUS_HOME", directory.path().join("argus-engine"));
            }
            if explicit_home == "product" {
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
            ("argus", true) => "argus-desktop-preview-1.0.0".to_owned(),
            (_, false) => format!("{profile}-desktop"),
            (_, true) => format!("{profile}-desktop-preview"),
        };
        let legacy = base.join("Argus").join("settings.json");
        std::fs::create_dir_all(legacy.parent().unwrap()).unwrap();
        let legacy_bytes = br#"{"token":"legacy-token","runnerConfigured":true,"runnerKind":"copilot"}"#;
        std::fs::write(&legacy, legacy_bytes).unwrap();
        let existing = base.join("argus-desktop/logs/desktop.log");
        std::fs::create_dir_all(existing.parent().unwrap()).unwrap();
        std::fs::write(&existing, b"existing data must not move").unwrap();
        let store = super::SettingsStore::open().unwrap();
        assert_eq!(store.data_dir(), base.join(name));
        if profile == "argus" && !preview {
            assert_eq!(store.snapshot().token, "legacy-token");
        } else {
            assert_ne!(store.snapshot().token, "legacy-token");
            assert!(!store.snapshot().runner_configured);
            assert!(!store.data_dir().join("logs/desktop.log").exists());
        }
        // Argus's state root: the product's own variable or home directory, never another product's.
        let expected_home = if preview { store.data_dir().join("argus-home") }
            else if let Some(explicit) = std::env::var_os(format!("{}_HOME", profile.to_ascii_uppercase())) {
                std::path::PathBuf::from(explicit)
            }
            else { root.join(format!(".{profile}")) };
        assert_eq!(crate::runner::argus_home_dir(), expected_home);
        assert_eq!(std::fs::read(&legacy).unwrap(), legacy_bytes);
        assert_eq!(std::fs::read(&existing).unwrap(), b"existing data must not move");
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
            let original = format!(r#"{{"appearanceTheme":"dark","runnerKind":"pi","runnerConfigured":true,"setupComplete":true,"startupEyeMotion":"{choice}"}}"#);
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
            r#"{"runnerKind":"copilot","runnerConfigured":true,"setupComplete":true}"#,
        )
        .unwrap();
        let (settings, _) = super::load_settings_file(&file).unwrap();
        assert_eq!(settings.runner_kind, crate::models::RunnerKind::Copilot);
        assert!(settings.runner_configured);
        assert!(settings.setup_complete);
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
