use crate::models::{PiConfiguration, RunnerKind, RUNNER_KINDS};
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
    use super::{first_node_runtime_dir, is_node_batch_wrapper, nvm_node_dirs};
    use crate::models::RunnerKind;
    use std::{fs, path::Path};

    #[test]
    fn all_supported_runner_labels_are_stable() {
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
