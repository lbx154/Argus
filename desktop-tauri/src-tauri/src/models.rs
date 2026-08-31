use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const RUNNER_KINDS: [&str; 9] = [
    "codex", "claude", "copilot", "cursor", "pi", "opencode", "grok", "qoder", "dsh",
];

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum RunnerKind {
    #[default]
    Codex,
    Claude,
    Copilot,
    Cursor,
    Pi,
    Opencode,
    Grok,
    Qoder,
    Dsh,
}

impl RunnerKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Codex => "codex",
            Self::Claude => "claude",
            Self::Copilot => "copilot",
            Self::Cursor => "cursor",
            Self::Pi => "pi",
            Self::Opencode => "opencode",
            Self::Grok => "grok",
            Self::Qoder => "qoder",
            Self::Dsh => "dsh",
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            Self::Codex => "Codex CLI",
            Self::Claude => "Claude Code",
            Self::Copilot => "GitHub Copilot CLI",
            Self::Cursor => "Cursor CLI",
            Self::Pi => "Pi (follows your Pi model)",
            Self::Opencode => "OpenCode",
            Self::Grok => "Grok Build",
            Self::Qoder => "Qoder CLI",
            Self::Dsh => "DeepSeek Harness",
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum AppearanceTheme {
    #[default]
    System,
    Light,
    Dark,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopSettings {
    #[serde(default = "default_host")]
    pub host: String,
    #[serde(default = "default_port")]
    pub port: u16,
    #[serde(default)]
    pub token: String,
    #[serde(default)]
    pub runner_kind: RunnerKind,
    #[serde(default)]
    pub runner_bins: BTreeMap<String, String>,
    #[serde(default)]
    pub runner_configured: bool,
    #[serde(default)]
    pub setup_complete: bool,
    #[serde(default)]
    pub appearance_theme: AppearanceTheme,
}

fn default_host() -> String {
    "127.0.0.1".to_owned()
}

const fn default_port() -> u16 {
    8799
}

impl Default for DesktopSettings {
    fn default() -> Self {
        Self {
            host: default_host(),
            port: default_port(),
            token: String::new(),
            runner_kind: RunnerKind::default(),
            runner_bins: BTreeMap::new(),
            // The default runner is auto-detected at launch. Setup is an
            // editable preference panel, not a blocking first-run gate.
            runner_configured: true,
            setup_complete: true,
            appearance_theme: AppearanceTheme::default(),
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum BackendState {
    #[default]
    Idle,
    Starting,
    Ready,
    Error,
    Stopped,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendStatus {
    pub state: BackendState,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pid: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
}

impl Default for BackendStatus {
    fn default() -> Self {
        Self {
            state: BackendState::Idle,
            message: "尚未启动".to_owned(),
            detail: None,
            pid: None,
            url: None,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PiConfiguration {
    pub config_dir: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provider: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub qualified_model: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopReleaseIdentity {
    pub package_version: String,
    pub release_id: String,
    pub source_digest: String,
    pub distribution: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopRuntimeIdentity {
    pub state: BackendState,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pid: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopSetup {
    pub complete: bool,
    pub host: String,
    pub port: u16,
    pub runner_kind: RunnerKind,
    pub runner_bins: BTreeMap<String, String>,
    pub runner_configured: bool,
    pub detected_runners: BTreeMap<String, String>,
    pub pi_configuration: PiConfiguration,
    pub release_identity: DesktopReleaseIdentity,
    pub runtime_identity: DesktopRuntimeIdentity,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CompleteSetupInput {
    pub port: u32,
    pub runner_kind: RunnerKind,
    #[serde(default)]
    pub runner_bins: BTreeMap<String, String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SetupResult {
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl SetupResult {
    pub fn ok() -> Self {
        Self {
            ok: true,
            error: None,
        }
    }

    pub fn error(message: impl Into<String>) -> Self {
        Self {
            ok: false,
            error: Some(message.into()),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopAppearance {
    pub theme: AppearanceTheme,
    pub resolved_theme: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DeliveryNotificationInput {
    pub delivery_id: String,
    pub title: String,
    pub summary: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
}

impl DeliveryNotificationInput {
    pub fn bounded(self) -> Option<Self> {
        let delivery_id = self
            .delivery_id
            .trim()
            .chars()
            .take(300)
            .collect::<String>();
        if delivery_id.is_empty() {
            return None;
        }
        let title = self.title.trim().chars().take(240).collect::<String>();
        let summary = self.summary.trim().chars().take(1_000).collect::<String>();
        let path = self.path.and_then(|value| {
            let trimmed = value.trim().chars().take(1_000).collect::<String>();
            (!trimmed.is_empty()).then_some(trimmed)
        });
        Some(Self {
            delivery_id,
            title: if title.is_empty() {
                "Argus".to_owned()
            } else {
                title
            },
            summary,
            path,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum UpdateState {
    Idle,
    Checking,
    UpToDate,
    Available,
    Downloading,
    Installing,
    Error,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateStatus {
    pub state: UpdateState,
    pub current_version: String,
    /// True only for a user-initiated menu/card action. Background checks must
    /// never surface checking, up-to-date, or transient-error UI.
    #[serde(default)]
    pub user_initiated: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub available_version: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub notes: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub progress: Option<u8>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

impl Default for UpdateStatus {
    fn default() -> Self {
        Self {
            state: UpdateState::Idle,
            current_version: env!("CARGO_PKG_VERSION").to_owned(),
            user_initiated: false,
            available_version: None,
            notes: None,
            progress: None,
            detail: None,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendOwnership {
    pub schema: u32,
    pub pid: u32,
    pub root_pid: u32,
    pub host: String,
    pub port: u16,
    pub executable: String,
    pub manifest_source_digest: String,
    pub token_sha256: String,
    pub started_at: String,
}

#[derive(Clone, Debug)]
pub struct ProbeIdentity {
    pub compatible: bool,
    pub occupied: bool,
    pub authenticated: bool,
    pub detail: Option<String>,
    pub pid: Option<u32>,
    pub executable: Option<String>,
    pub manifest_source_digest: Option<String>,
    pub started_at: Option<String>,
    pub launch_nonce: Option<String>,
    pub failure_kind: Option<ProbeFailureKind>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProbeFailureKind {
    Timeout,
    Network,
    Http,
    Identity,
}

#[cfg(test)]
mod tests {
    use super::DesktopSettings;

    #[test]
    fn fresh_desktop_settings_do_not_block_cockpit_startup() {
        let settings = DesktopSettings::default();
        assert!(settings.runner_configured);
        assert!(settings.setup_complete);
    }
}
