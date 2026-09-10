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
/// the Web token and can prove ownership of a prior bundled backend.
const CANONICAL_USER_DATA_DIR: &str = "argus-desktop";

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
        if !crate::release::preview_mode() {
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

    pub fn cockpit_url(settings: &DesktopSettings) -> String {
        let token: String =
            url::form_urlencoded::byte_serialize(settings.token.as_bytes()).collect();
        let theme = match settings.appearance_theme {
            AppearanceTheme::System => "system", AppearanceTheme::Light => "light", AppearanceTheme::Dark => "dark",
        };
        format!("{}/?token={token}&desktopTheme={theme}", Self::api_base_url(settings))
    }
}

pub(crate) fn desktop_data_dir() -> PathBuf {
    let name = if crate::release::preview_mode() { "argus-desktop-preview" } else { CANONICAL_USER_DATA_DIR };
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
    use super::{normalized_runner_bins, random_token};
    use std::collections::BTreeMap;

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
