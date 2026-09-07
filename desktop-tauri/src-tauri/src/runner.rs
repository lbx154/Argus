use crate::models::{DesktopSettings, PiConfiguration, RunnerKind, RUNNER_KINDS};
use crate::settings::runner_bin;
use anyhow::Context;
use serde_json::Value;
use std::{
    collections::BTreeMap,
    env, fs,
    path::{Path, PathBuf},
};

fn home_dir() -> PathBuf {
    env::var_os("USERPROFILE")
        .or_else(|| env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

pub fn argus_home_dir() -> PathBuf {
    env::var_os("ARGUS_SKILL_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join(".argus-skill"))
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RunnerConfiguration {
    pub kind: RunnerKind,
    pub executable: Option<String>,
}

fn configured_backend(values: &BTreeMap<String, String>) -> Option<&str> {
    ["ARGUS_SKILL_RUNNER_BACKEND", "ARGUS_SKILL_LIFE_BACKEND"]
        .iter()
        .find_map(|key| {
            values
                .get(*key)
                .map(|value| value.trim())
                .filter(|value| !value.is_empty())
        })
}

fn parse_runner_kind(name: &str) -> Option<RunnerKind> {
    serde_json::from_value(Value::String(name.to_ascii_lowercase())).ok()
}

fn configured_bin(values: &BTreeMap<String, String>) -> Option<String> {
    values
        .get("ARGUS_SKILL_RUNNER_BIN")
        .map(|value| value.trim())
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn select_runner_configuration(
    settings: &DesktopSettings,
    environment: &BTreeMap<String, String>,
    persisted: &BTreeMap<String, String>,
) -> Option<RunnerConfiguration> {
    let environment_backend = configured_backend(environment);
    let environment_kind = environment_backend.and_then(parse_runner_kind);
    let persisted_backend = configured_backend(persisted);
    let persisted_kind = persisted_backend.and_then(parse_runner_kind);
    let kind = if settings.runner_configured {
        settings.runner_kind.clone()
    } else {
        parse_runner_kind(environment_backend.or(persisted_backend)?)?
    };
    let manual = settings
        .runner_configured
        .then(|| runner_bin(settings, &kind))
        .flatten();
    let executable = manual
        .or_else(|| {
            (environment_kind.is_none() || environment_kind.as_ref() == Some(&kind))
                .then(|| configured_bin(environment))
                .flatten()
        })
        .or_else(|| {
            (persisted_kind.as_ref() == Some(&kind))
                .then(|| configured_bin(persisted))
                .flatten()
        });
    Some(RunnerConfiguration { kind, executable })
}

fn read_shared_configuration(path: &Path) -> anyhow::Result<BTreeMap<String, String>> {
    let raw = match fs::read_to_string(path) {
        Ok(raw) => raw,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(BTreeMap::new()),
        Err(error) => return Err(error).with_context(|| format!("Cannot read {}", path.display())),
    };
    let values = serde_json::from_str::<BTreeMap<String, Value>>(&raw)
        .with_context(|| format!("Invalid shared Argus configuration at {}", path.display()))?;
    Ok(values
        .into_iter()
        .filter_map(|(key, value)| value.as_str().map(|value| (key, value.to_owned())))
        .collect())
}

pub fn resolve_runner_configuration(
    settings: &DesktopSettings,
) -> anyhow::Result<Option<RunnerConfiguration>> {
    let environment = env::vars().collect();
    let persisted = read_shared_configuration(&argus_home_dir().join("config.json"))?;
    let Some(mut configured) = select_runner_configuration(settings, &environment, &persisted)
    else {
        return Ok(None);
    };
    if configured.executable.is_none() {
        configured.executable = resolve_runner_binary(&configured.kind);
    } else if let Some(requested) = configured.executable.as_ref() {
        if !Path::new(requested).is_file() {
            let candidates = if cfg!(windows) && Path::new(requested).extension().is_none() {
                vec![
                    format!("{requested}.cmd"),
                    format!("{requested}.exe"),
                    requested.clone(),
                ]
            } else {
                vec![requested.clone()]
            };
            let command_names: Vec<&str> = candidates.iter().map(String::as_str).collect();
            let resolved = env::var_os("PATH")
                .and_then(|path| {
                    env::split_paths(&path)
                        .find_map(|directory| first_file(&directory, &command_names))
                })
                .map(|path| path.to_string_lossy().into_owned())
                .or_else(|| {
                    (requested == configured.kind.as_str())
                        .then(|| resolve_runner_binary(&configured.kind))
                        .flatten()
                });
            if let Some(resolved) = resolved {
                configured.executable = Some(resolved);
            }
        }
    }
    Ok(Some(configured))
}

pub fn desktop_setup_complete(
    settings: &DesktopSettings,
    configured: Option<&RunnerConfiguration>,
) -> bool {
    configured
        .and_then(|runner| runner.executable.as_deref())
        .is_some_and(|executable| Path::new(executable).is_file())
        && (settings.setup_complete || !settings.runner_configured)
}

fn app_data_dir() -> PathBuf {
    env::var_os("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join("AppData").join("Roaming"))
}

fn local_app_data_dir() -> PathBuf {
    env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join("AppData").join("Local"))
}

fn names(kind: &RunnerKind) -> &'static [&'static str] {
    match kind {
        RunnerKind::Codex => &["codex.cmd", "codex.exe", "codex"],
        RunnerKind::Claude => &["claude.cmd", "claude.exe", "claude"],
        RunnerKind::Copilot => &["copilot.cmd", "copilot.exe", "copilot"],
        RunnerKind::Cursor => &[
            "agent.cmd",
            "agent.exe",
            "agent",
            "cursor-agent.cmd",
            "cursor-agent.exe",
            "cursor-agent",
        ],
        RunnerKind::Pi => &["pi.cmd", "pi.exe", "pi"],
        RunnerKind::Opencode => &["opencode.cmd", "opencode.exe", "opencode"],
        RunnerKind::Grok => &["grok.cmd", "grok.exe", "grok"],
        RunnerKind::Qoder => &["qodercli.cmd", "qodercli.exe", "qodercli"],
        RunnerKind::Dsh => &["dsh.cmd", "dsh.exe", "dsh"],
    }
}

fn first_file(directory: &Path, names: &[&str]) -> Option<PathBuf> {
    names
        .iter()
        .map(|name| directory.join(name))
        .find(|candidate| candidate.is_file())
}

/// npm's Windows shims (for example `%APPDATA%\\npm\\codex.cmd`) execute a
/// bare `node`.  A GUI process may have inherited a PATH from before Node/nvm
/// was installed even though it can still discover the shim by absolute path.
/// Resolve a real Node directory before handing that shim to the frozen Python
/// backend; otherwise every Manager turn exits before reaching its provider.
fn is_node_batch_wrapper(path: &Path) -> bool {
    path.extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| {
            extension.eq_ignore_ascii_case("cmd") || extension.eq_ignore_ascii_case("bat")
        })
}

fn node_in_directory(directory: &Path) -> bool {
    directory.join("node.exe").is_file() || directory.join("node").is_file()
}

fn nvm_node_dirs(settings: &Path) -> Vec<PathBuf> {
    fs::read_to_string(settings)
        .ok()
        .into_iter()
        .flat_map(|contents| contents.lines().map(str::to_owned).collect::<Vec<_>>())
        .filter_map(|line| {
            let (key, value) = line.split_once(':')?;
            let key = key.trim();
            (key.eq_ignore_ascii_case("path") || key.eq_ignore_ascii_case("symlink"))
                .then(|| value.trim().trim_matches('"'))
                .filter(|value| !value.is_empty())
                .map(PathBuf::from)
        })
        .collect()
}

fn first_node_runtime_dir(paths: impl IntoIterator<Item = PathBuf>) -> Option<PathBuf> {
    paths
        .into_iter()
        .find(|directory| node_in_directory(directory))
}

fn node_runtime_candidates(runner: &Path) -> Vec<PathBuf> {
    let home = home_dir();
    let app_data = app_data_dir();
    let local_app_data = local_app_data_dir();
    let mut candidates = Vec::new();
    if let Some(parent) = runner.parent() {
        candidates.push(parent.to_path_buf());
    }
    if let Some(path) = env::var_os("PATH") {
        candidates.extend(env::split_paths(&path));
    }
    for name in ["NVM_SYMLINK", "NVM_HOME"] {
        if let Some(path) = env::var_os(name).filter(|path| !path.is_empty()) {
            candidates.push(PathBuf::from(path));
        }
    }
    let mut nvm_settings = vec![
        local_app_data.join("nvm").join("settings.txt"),
        app_data.join("nvm").join("settings.txt"),
        home.join("AppData")
            .join("Local")
            .join("nvm")
            .join("settings.txt"),
    ];
    if let Some(nvm_home) = env::var_os("NVM_HOME").filter(|path| !path.is_empty()) {
        nvm_settings.push(PathBuf::from(nvm_home).join("settings.txt"));
    }
    for settings in nvm_settings {
        candidates.extend(nvm_node_dirs(&settings));
    }
    for name in ["ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"] {
        if let Some(root) = env::var_os(name).filter(|path| !path.is_empty()) {
            candidates.push(PathBuf::from(root).join("nodejs"));
        }
    }
    candidates.extend([
        local_app_data.join("Volta").join("bin"),
        local_app_data.join("nvs").join("default"),
        home.join("scoop")
            .join("apps")
            .join("nodejs")
            .join("current"),
    ]);
    candidates
}

/// Return PATH entries needed to launch an npm batch wrapper.  The returned
/// directory is verified to contain Node, so prepending it cannot mask a
/// missing/foreign command with an arbitrary PATH element.
pub fn runner_runtime_path_entries(runner: &str) -> Vec<PathBuf> {
    let runner = Path::new(runner);
    if !is_node_batch_wrapper(runner) {
        return Vec::new();
    }
    first_node_runtime_dir(node_runtime_candidates(runner))
        .into_iter()
        .collect()
}

pub fn resolve_runner_binary(kind: &RunnerKind) -> Option<String> {
    let names = names(kind);
    let home = home_dir();
    let app_data = app_data_dir();
    let local_app_data = local_app_data_dir();
    let mut candidates = vec![app_data.join("npm"), home.join(".local").join("bin")];

    match kind {
        RunnerKind::Codex => candidates.push(local_app_data.join("Microsoft").join("WindowsApps")),
        RunnerKind::Claude => candidates.push(local_app_data.join("Programs").join("claude-code")),
        RunnerKind::Copilot => {
            candidates.push(local_app_data.join("Programs").join("github-copilot-cli"))
        }
        RunnerKind::Cursor => candidates.push(local_app_data.join("cursor-agent")),
        RunnerKind::Opencode => {
            candidates.push(home.join(".opencode").join("bin"));
            candidates.push(local_app_data.join("Programs").join("opencode"));
        }
        RunnerKind::Grok => candidates.push(local_app_data.join("Programs").join("grok")),
        RunnerKind::Pi | RunnerKind::Qoder | RunnerKind::Dsh => {}
    }

    if let Some(path) = env::var_os("PATH") {
        candidates.extend(env::split_paths(&path).filter(|path| {
            !path
                .to_string_lossy()
                .to_ascii_lowercase()
                .contains("windowsapps")
        }));
    }

    candidates
        .iter()
        .find_map(|directory| first_file(directory, names))
        .map(|path| path.to_string_lossy().into_owned())
}

pub fn detect_runners() -> BTreeMap<String, String> {
    RUNNER_KINDS
        .iter()
        .filter_map(|name| {
            let kind = match *name {
                "codex" => RunnerKind::Codex,
                "claude" => RunnerKind::Claude,
                "copilot" => RunnerKind::Copilot,
                "cursor" => RunnerKind::Cursor,
                "pi" => RunnerKind::Pi,
                "opencode" => RunnerKind::Opencode,
                "grok" => RunnerKind::Grok,
                "qoder" => RunnerKind::Qoder,
                "dsh" => RunnerKind::Dsh,
                _ => return None,
            };
            resolve_runner_binary(&kind).map(|path| ((*name).to_owned(), path))
        })
        .collect()
}

fn pi_config_dir() -> PathBuf {
    let configured = env::var("PI_CODING_AGENT_DIR").unwrap_or_default();
    let configured = configured.trim();
    if configured.is_empty() {
        return home_dir().join(".pi").join("agent");
    }
    if configured == "~" {
        return home_dir();
    }
    if let Some(relative) = configured
        .strip_prefix("~/")
        .or_else(|| configured.strip_prefix("~\\"))
    {
        return home_dir().join(relative);
    }
    PathBuf::from(configured)
}

/// Reads only public Pi model routing fields; secrets are never opened.
pub fn detect_pi_configuration() -> PiConfiguration {
    let config_dir = pi_config_dir();
    let mut result = PiConfiguration {
        config_dir: config_dir.to_string_lossy().into_owned(),
        provider: None,
        model: None,
        qualified_model: None,
    };
    let Some(value) = fs::read_to_string(config_dir.join("settings.json"))
        .ok()
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
    else {
        return result;
    };
    let provider = value
        .get("defaultProvider")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned);
    let model = value
        .get("defaultModel")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned);
    result.qualified_model = model.as_ref().map(|model| {
        if let Some(provider) = provider
            .as_ref()
            .filter(|provider| !model.starts_with(&format!("{provider}/")))
        {
            format!("{provider}/{model}")
        } else {
            model.clone()
        }
    });
    result.provider = provider;
    result.model = model;
    result
}

#[cfg(test)]
mod tests {
    use super::{
        desktop_setup_complete, first_node_runtime_dir, is_node_batch_wrapper, nvm_node_dirs,
        read_shared_configuration, select_runner_configuration, RunnerConfiguration,
    };
    use crate::models::{DesktopSettings, RunnerKind};
    use std::{collections::BTreeMap, fs, path::Path};

    #[test]
    fn missing_shared_configuration_is_an_unconfigured_installation() {
        let directory = tempfile::tempdir().unwrap();
        assert!(
            read_shared_configuration(&directory.path().join("config.json"))
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn corrupt_shared_configuration_is_reported_instead_of_discarded() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("config.json");
        for contents in ["{broken", "[]"] {
            fs::write(&path, contents).unwrap();
            let error = read_shared_configuration(&path).unwrap_err().to_string();
            assert!(error.contains("Invalid shared Argus configuration"));
            assert!(error.contains("config.json"));
        }
    }

    #[test]
    fn fresh_desktop_reuses_persisted_claude_and_copilot_without_saving_a_desktop_choice() {
        for (backend, expected) in [
            ("claude", RunnerKind::Claude),
            ("copilot", RunnerKind::Copilot),
        ] {
            let settings = DesktopSettings::default();
            let persisted = BTreeMap::from([
                ("ARGUS_SKILL_RUNNER_BACKEND".to_owned(), backend.to_owned()),
                (
                    "ARGUS_SKILL_RUNNER_BIN".to_owned(),
                    format!("C:/agents/{backend}.cmd"),
                ),
            ]);
            let selected =
                select_runner_configuration(&settings, &BTreeMap::new(), &persisted).unwrap();
            assert_eq!(selected.kind, expected);
            assert_eq!(
                selected.executable,
                Some(format!("C:/agents/{backend}.cmd"))
            );
            assert!(!settings.runner_configured);
            assert!(!settings.setup_complete);
        }
    }

    #[test]
    fn explicit_desktop_choice_is_authoritative_over_another_shared_backend() {
        let settings = DesktopSettings {
            runner_kind: RunnerKind::Copilot,
            runner_configured: true,
            setup_complete: true,
            ..DesktopSettings::default()
        };
        let shared = BTreeMap::from([
            ("ARGUS_SKILL_RUNNER_BACKEND".to_owned(), "claude".to_owned()),
            (
                "ARGUS_SKILL_RUNNER_BIN".to_owned(),
                "C:/agents/claude.cmd".to_owned(),
            ),
        ]);
        let selected = select_runner_configuration(&settings, &shared, &shared).unwrap();
        assert_eq!(selected.kind, RunnerKind::Copilot);
        assert_eq!(selected.executable, None);
    }

    #[test]
    fn inherited_environment_backend_overrides_persisted_backend_without_reusing_its_binary() {
        let environment =
            BTreeMap::from([("ARGUS_SKILL_LIFE_BACKEND".to_owned(), "copilot".to_owned())]);
        let persisted = BTreeMap::from([
            ("ARGUS_SKILL_RUNNER_BACKEND".to_owned(), "claude".to_owned()),
            (
                "ARGUS_SKILL_RUNNER_BIN".to_owned(),
                "C:/agents/claude.cmd".to_owned(),
            ),
        ]);
        let selected =
            select_runner_configuration(&DesktopSettings::default(), &environment, &persisted)
                .unwrap();
        assert_eq!(selected.kind, RunnerKind::Copilot);
        assert_eq!(selected.executable, None);
    }

    #[test]
    fn fresh_unconfigured_desktop_does_not_select_codex() {
        let settings = DesktopSettings::default();
        assert!(
            select_runner_configuration(&settings, &BTreeMap::new(), &BTreeMap::new()).is_none()
        );
        assert!(!desktop_setup_complete(&settings, None));
    }

    #[test]
    fn an_unknown_environment_backend_does_not_silently_choose_a_persisted_backend() {
        let environment = BTreeMap::from([(
            "ARGUS_SKILL_RUNNER_BACKEND".to_owned(),
            "copilto".to_owned(),
        )]);
        let persisted =
            BTreeMap::from([("ARGUS_SKILL_RUNNER_BACKEND".to_owned(), "claude".to_owned())]);
        assert!(
            select_runner_configuration(&DesktopSettings::default(), &environment, &persisted,)
                .is_none()
        );
    }

    #[test]
    fn explicit_desktop_binary_wins_over_shared_binary() {
        let settings = DesktopSettings {
            runner_kind: RunnerKind::Claude,
            runner_configured: true,
            runner_bins: BTreeMap::from([(
                "claude".to_owned(),
                "C:/desktop/claude.cmd".to_owned(),
            )]),
            ..DesktopSettings::default()
        };
        let shared = BTreeMap::from([
            ("ARGUS_SKILL_RUNNER_BACKEND".to_owned(), "claude".to_owned()),
            (
                "ARGUS_SKILL_RUNNER_BIN".to_owned(),
                "C:/shared/claude.cmd".to_owned(),
            ),
        ]);
        let selected = select_runner_configuration(&settings, &shared, &shared).unwrap();
        assert_eq!(
            selected.executable.as_deref(),
            Some("C:/desktop/claude.cmd")
        );
    }

    #[test]
    fn inherited_backend_skips_onboarding_only_when_its_binary_exists() {
        let directory = tempfile::tempdir().unwrap();
        let executable = directory.path().join("claude.cmd");
        let configured = RunnerConfiguration {
            kind: RunnerKind::Claude,
            executable: Some(executable.to_string_lossy().into_owned()),
        };
        let settings = DesktopSettings::default();
        assert!(!desktop_setup_complete(&settings, Some(&configured)));
        fs::write(executable, b"test-cli").unwrap();
        assert!(desktop_setup_complete(&settings, Some(&configured)));
        let unfinished = DesktopSettings {
            runner_configured: true,
            ..settings
        };
        assert!(!desktop_setup_complete(&unfinished, Some(&configured)));
    }

    #[test]
    fn all_supported_runner_labels_are_stable() {
        assert_eq!(RunnerKind::Cursor.label(), "Cursor CLI");
        assert_eq!(RunnerKind::Opencode.label(), "OpenCode");
        assert_eq!(RunnerKind::Grok.label(), "Grok Build");
        assert_eq!(RunnerKind::Qoder.label(), "Qoder CLI");
        assert_eq!(RunnerKind::Dsh.label(), "DeepSeek Harness");
    }

    #[test]
    fn npm_batch_wrapper_requires_node_but_native_runner_does_not() {
        assert!(is_node_batch_wrapper(Path::new("codex.cmd")));
        assert!(is_node_batch_wrapper(Path::new("CLAUDE.BAT")));
        assert!(!is_node_batch_wrapper(Path::new("codex.exe")));
        assert!(!is_node_batch_wrapper(Path::new("pi")));
    }

    #[test]
    fn nvm_settings_yield_a_verified_node_runtime_directory() {
        let temporary = tempfile::tempdir().unwrap();
        let node_dir = temporary.path().join("nodejs");
        fs::create_dir_all(&node_dir).unwrap();
        fs::write(node_dir.join("node.exe"), b"test-node").unwrap();
        let settings = temporary.path().join("settings.txt");
        fs::write(
            &settings,
            format!("root: ignored\npath: {}\n", node_dir.display()),
        )
        .unwrap();

        let candidates = nvm_node_dirs(&settings);
        assert_eq!(candidates, vec![node_dir.clone()]);
        assert_eq!(
            first_node_runtime_dir(candidates).as_deref(),
            Some(node_dir.as_path())
        );
    }
}
