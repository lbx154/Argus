use crate::models::{
    AppearanceTheme, DesktopAppearance, DesktopSettings, RunnerKind, RUNNER_KINDS,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::RngCore;
use serde_json::Value;
use std::{
    collections::BTreeMap,
    env, fs,
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
        migrate_legacy_settings(&data_dir, &settings_path)?;
        let (settings, should_save) = load_settings_file(&settings_path);
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
        *self.settings.lock().expect("settings mutex poisoned") = settings;
        self.save()
    }

    pub fn set_appearance(&self, theme: AppearanceTheme) -> anyhow::Result<DesktopAppearance> {
        {
            let mut settings = self.settings.lock().expect("settings mutex poisoned");
            settings.appearance_theme = theme;
        }
        self.save()?;
        Ok(self.appearance())
    }

    pub fn appearance(&self) -> DesktopAppearance {
        let theme = self.snapshot().appearance_theme;
        DesktopAppearance {
            resolved_theme: match theme {
                AppearanceTheme::Dark => "dark".to_owned(),
                AppearanceTheme::Light | AppearanceTheme::System => "light".to_owned(),
            },
            theme,
        }
    }

    pub fn save(&self) -> anyhow::Result<()> {
        let payload = serde_json::to_vec_pretty(&self.snapshot())?;
        fs::create_dir_all(&self.data_dir)?;
        // This preserves the established settings shape and ACL inheritance. The
        // file contains a local bearer token, never diagnostics or release metadata.
        fs::write(&self.settings_path, payload)?;
        Ok(())
    }

    pub fn api_base_url(settings: &DesktopSettings) -> String {
        format!("http://{}:{}", settings.host, settings.port)
    }

    pub fn cockpit_url(settings: &DesktopSettings) -> String {
        let token: String =
            url::form_urlencoded::byte_serialize(settings.token.as_bytes()).collect();
        format!("{}/?token={token}", Self::api_base_url(settings))
    }
}

fn desktop_data_dir() -> PathBuf {
    let app_data = env::var_os("APPDATA")
        .map(PathBuf::from)
        .or_else(|| {
            env::var_os("USERPROFILE")
                .map(PathBuf::from)
                .map(|home| home.join("AppData").join("Roaming"))
        })
        .unwrap_or_else(|| PathBuf::from("."));
    app_data.join(CANONICAL_USER_DATA_DIR)
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

fn load_settings_file(path: &Path) -> (DesktopSettings, bool) {
    let mut settings = DesktopSettings::default();
    let mut needs_save = false;
    let parsed = fs::read_to_string(path)
        .ok()
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok());

    if let Some(value) = parsed.as_ref() {
        if let Ok(decoded) = serde_json::from_value::<DesktopSettings>(value.clone()) {
            settings = decoded;
        }
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
    if settings.host.is_empty() {
        settings.host = "127.0.0.1".to_owned();
        needs_save = true;
    }
    if settings.port == 0 {
        settings.port = 8799;
        needs_save = true;
    }
    settings.runner_bins = normalized_runner_bins(&settings.runner_bins);
    // The local backend can use the default runner or auto-detection. Never
    // interrupt normal startup with a mandatory launcher wizard; users can
    // open desktop settings whenever they want to make an explicit choice.
    if !settings.runner_configured || !settings.setup_complete {
        settings.runner_configured = true;
        settings.setup_complete = true;
        needs_save = true;
    }
    if settings.token.trim().is_empty() {
        settings.token = random_token();
        needs_save = true;
    }
    (settings, needs_save)
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
    fn legacy_unfinished_onboarding_is_migrated_to_nonblocking_defaults() {
        let directory = tempfile::tempdir().unwrap();
        let file = directory.path().join("settings.json");
        std::fs::write(&file, r#"{"runnerConfigured":false,"setupComplete":false}"#).unwrap();
        let (settings, needs_save) = super::load_settings_file(&file);
        assert!(needs_save);
        assert!(settings.runner_configured);
        assert!(settings.setup_complete);
    }
}
