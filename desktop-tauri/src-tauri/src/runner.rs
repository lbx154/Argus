use crate::models::{DesktopSettings, PiConfiguration, RunnerKind, RUNNER_KINDS};
use crate::settings::runner_bin;
use serde_json::Value;
use std::{
    collections::BTreeMap,
    env, fs,
    path::{Path, PathBuf},
};

/// The oldest Node.js major the desktop runs Argus with.
pub const NODE_MAJOR: u32 = 22;
const NODE_DOWNLOAD: &str = "https://nodejs.org/";

fn home_dir() -> PathBuf {
    env::var_os("USERPROFILE")
        .or_else(|| env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// The state root of the desktop's Argus, given to it as `ARGUS_HOME`:
/// `$<PROFILE>_HOME` or `~/.<profile>` (`~/.argus`); a preview keeps its own.
pub fn argus_home_dir() -> PathBuf {
    if crate::release::preview_mode() {
        return crate::settings::desktop_data_dir().join("argus-home");
    }
    let profile = crate::settings::desktop_profile();
    env::var_os(format!("{}_HOME", profile.to_ascii_uppercase()))
        .filter(|value| !value.to_string_lossy().trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join(format!(".{profile}")))
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RunnerConfiguration {
    pub kind: RunnerKind,
    pub executable: Option<String>,
}

/// The CLI the operator chose in the desktop, found on disk. Nothing is
/// chosen until they confirm one.
pub fn resolve_runner_configuration(settings: &DesktopSettings) -> Option<RunnerConfiguration> {
    if !settings.runner_configured {
        return None;
    }
    let kind = settings.runner_kind.clone();
    let executable = match runner_bin(settings, &kind) {
        Some(requested) if Path::new(&requested).is_file() => Some(requested),
        // A bare command name is looked up as the shell would.
        Some(requested) => {
            let candidates = if cfg!(windows) && Path::new(&requested).extension().is_none() {
                vec![format!("{requested}.cmd"), format!("{requested}.exe"), requested.clone()]
            } else {
                vec![requested.clone()]
            };
            env::var_os("PATH")
                .and_then(|path| env::split_paths(&path).find_map(|directory| first_file(&directory, &candidates)))
                .map(|path| path.to_string_lossy().into_owned())
                .or_else(|| (requested == kind.as_str()).then(|| resolve_runner_binary(&kind)).flatten())
        }
        None => resolve_runner_binary(&kind),
    };
    Some(RunnerConfiguration { kind, executable })
}

pub fn desktop_setup_complete(
    settings: &DesktopSettings,
    configured: Option<&RunnerConfiguration>,
) -> bool {
    settings.runner_configured
        && settings.setup_complete
        && configured
            .and_then(|runner| runner.executable.as_deref())
            .is_some_and(|executable| Path::new(executable).is_file())
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

fn first_file(directory: &Path, names: &[String]) -> Option<PathBuf> {
    names
        .iter()
        .map(|name| directory.join(name))
        .find(|candidate| candidate.is_file())
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

/// Where Node.js and the CLIs npm installs beside it usually live. A desktop
/// app does not inherit a login shell's PATH (macOS starts it with only the
/// system directories), so these are searched as well as PATH.
fn install_dirs() -> Vec<PathBuf> {
    let home = home_dir();
    let app_data = app_data_dir();
    let local_app_data = local_app_data_dir();
    let mut candidates = Vec::new();
    for name in ["NVM_SYMLINK", "NVM_HOME"] {
        if let Some(path) = env::var_os(name).filter(|path| !path.is_empty()) {
            candidates.push(PathBuf::from(path));
        }
    }
    let mut nvm_settings = vec![
        local_app_data.join("nvm").join("settings.txt"),
        app_data.join("nvm").join("settings.txt"),
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
        home.join("scoop").join("apps").join("nodejs").join("current"),
        PathBuf::from("/opt/homebrew/bin"),
        PathBuf::from("/usr/local/bin"),
        home.join(".volta").join("bin"),
    ]);
    // nvm on macOS and Linux: one directory per installed version, newest first.
    let mut versions: Vec<PathBuf> = fs::read_dir(home.join(".nvm").join("versions").join("node"))
        .into_iter()
        .flatten()
        .flatten()
        .map(|entry| entry.path().join("bin"))
        .collect();
    versions.sort_by(|a, b| b.cmp(a));
    candidates.extend(versions);
    candidates
}

fn path_dirs() -> Vec<PathBuf> {
    env::var_os("PATH").map(|path| env::split_paths(&path).collect()).unwrap_or_default()
}

/// `vMAJOR.MINOR.PATCH`, as `node --version` prints it.
fn node_major(version: &str) -> Option<u32> {
    version.trim().strip_prefix('v')?.split('.').next()?.parse().ok()
}

fn node_version(node: &Path) -> Option<String> {
    let mut command = std::process::Command::new(node);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt as _;
        command.creation_flags(0x0800_0000);
    }
    let output = command.arg("--version").stdin(std::process::Stdio::null()).output().ok()?;
    output.status.success().then(|| String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

/// The first Node.js at least NODE_MAJOR in `directories`, or why there is none.
fn first_node(directories: impl IntoIterator<Item = PathBuf>) -> Result<PathBuf, String> {
    let name = if cfg!(windows) { "node.exe" } else { "node" };
    let mut older = None;
    for node in directories.into_iter().map(|directory| directory.join(name)).filter(|node| node.is_file()) {
        let Some(version) = node_version(&node) else { continue };
        if node_major(&version).is_some_and(|major| major >= NODE_MAJOR) {
            return Ok(node);
        }
        older.get_or_insert(format!("{}（{version}）", node.display()));
    }
    let found = match older {
        Some(older) => format!("本机的 Node.js 版本过低：{older}。"),
        None => "未在本机找到 Node.js。".to_owned(),
    };
    Err(format!(
        "{found}本地后端需要 Node.js {NODE_MAJOR} 或更高版本。请从 {NODE_DOWNLOAD} 下载安装 LTS 版本\
         （Codex、Claude Code、Copilot CLI 和 Pi 也都通过 npm 安装，同样需要它），安装后点击“重试”。"
    ))
}

/// The Node.js to run Argus with: beside the chosen CLI first (npm puts CLIs
/// next to Node with nvm, Volta and Homebrew), then PATH, then install_dirs.
pub fn find_node(runner: Option<&Path>) -> Result<PathBuf, String> {
    let beside = runner.and_then(Path::parent).map(Path::to_path_buf);
    first_node(beside.into_iter().chain(path_dirs()).chain(install_dirs()))
}

/// Packaged builds use their own Node, even when the operator has no system Node.
pub fn find_runtime_node(runner: Option<&Path>, resources: Option<&Path>) -> Result<PathBuf, String> {
    if let Some(resources) = resources {
        let directory = resources.join("node-runtime");
        if directory.is_dir() {
            return first_node([directory]);
        }
    }
    find_node(runner)
}

/// Resolve npm and Pi installer shims without cmd.exe reinterpreting arguments.
pub fn runner_launch(node: &Path, runner: &Path) -> Result<(PathBuf, Vec<String>), String> {
    let extension = runner.extension().and_then(|value| value.to_str()).unwrap_or("").to_ascii_lowercase();
    if extension == "js" || extension == "mjs" || extension == "cjs" {
        return Ok((node.to_path_buf(), vec![runner.to_string_lossy().into_owned()]));
    }
    if !cfg!(windows) || (extension != "cmd" && extension != "bat") {
        return Ok((runner.to_path_buf(), vec![]));
    }
    let contents = fs::read_to_string(runner).map_err(|error| error.to_string())?;
    let target = contents.split('"').filter_map(|piece| piece.strip_prefix("%~dp0").or_else(|| piece.strip_prefix("%dp0%")))
        .filter(|piece| !piece.is_empty() && !piece.contains('%')).last()
        .ok_or_else(|| "无法安全解析此批处理启动器，请选择 CLI 的 .exe 或 JavaScript 入口文件。".to_owned())?;
    let file = runner.parent().unwrap_or(Path::new(".")).join(target.trim_start_matches(['\\', '/']));
    if !file.is_file() { return Err(format!("CLI 入口文件不存在：{}", file.display())); }
    let native = file.extension().and_then(|value| value.to_str()).is_some_and(|value| value.eq_ignore_ascii_case("exe") || value.eq_ignore_ascii_case("com"));
    Ok(if native { (file, vec![]) } else { (node.to_path_buf(), vec![file.to_string_lossy().into_owned()]) })
}

pub fn resolve_runner_binary(kind: &RunnerKind) -> Option<String> {
    let name = kind.as_str();
    let commands: Vec<&str> = match kind {
        RunnerKind::Cursor => vec!["agent", "cursor-agent"],
        RunnerKind::Qoder => vec!["qodercli", "qoder"],
        _ => vec![name],
    };
    let names: Vec<String> = commands.into_iter().flat_map(|name| [format!("{name}.cmd"), format!("{name}.exe"), name.to_owned()]).collect();
    let home = home_dir();
    let local_app_data = local_app_data_dir();
    let mut candidates = vec![app_data_dir().join("npm"), home.join(".local").join("bin")];
    match kind {
        RunnerKind::Codex => candidates.push(local_app_data.join("Microsoft").join("WindowsApps")),
        RunnerKind::Claude => candidates.push(local_app_data.join("Programs").join("claude-code")),
        RunnerKind::Copilot => {
            candidates.push(local_app_data.join("Programs").join("github-copilot-cli"))
        }
        RunnerKind::Opencode => candidates.push(home.join(".opencode").join("bin")),
        RunnerKind::Cursor => candidates.push(home.join(".cursor").join("bin")),
        RunnerKind::Qoder => candidates.push(home.join(".qoder").join("bin")),
        RunnerKind::Pi | RunnerKind::Dsh | RunnerKind::Grok => {}
    }
    candidates.extend(path_dirs().into_iter().filter(|path| {
        !path.to_string_lossy().to_ascii_lowercase().contains("windowsapps")
    }));
    candidates.extend(install_dirs());
    candidates
        .iter()
        .find_map(|directory| first_file(directory, &names))
        .map(|path| path.to_string_lossy().into_owned())
}

pub fn detect_runners() -> BTreeMap<String, String> {
    RUNNER_KINDS
        .iter()
        .filter_map(|kind| resolve_runner_binary(kind).map(|path| (kind.as_str().to_owned(), path)))
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
        desktop_setup_complete, node_major, nvm_node_dirs, resolve_runner_configuration,
        RunnerConfiguration,
    };
    use crate::models::{DesktopSettings, RunnerKind};
    use std::{collections::BTreeMap, fs};

    #[test]
    fn a_fresh_desktop_selects_no_cli() {
        let settings = DesktopSettings::default();
        assert!(resolve_runner_configuration(&settings).is_none());
        assert!(!desktop_setup_complete(&settings, None));
    }

    #[test]
    fn the_desktop_choice_names_the_cli() {
        let directory = tempfile::tempdir().unwrap();
        let executable = directory.path().join("claude.cmd");
        fs::write(&executable, b"test-cli").unwrap();
        let settings = DesktopSettings {
            runner_kind: RunnerKind::Claude,
            runner_configured: true,
            runner_bins: BTreeMap::from([("claude".to_owned(), executable.to_string_lossy().into_owned())]),
            ..DesktopSettings::default()
        };
        let selected = resolve_runner_configuration(&settings).unwrap();
        assert_eq!(selected.kind, RunnerKind::Claude);
        assert_eq!(selected.executable.as_deref(), Some(executable.to_string_lossy().as_ref()));
    }

    #[test]
    fn first_launch_requires_confirmation_even_with_an_inherited_cli() {
        let directory = tempfile::tempdir().unwrap();
        let executable = directory.path().join("claude.cmd");
        let configured = RunnerConfiguration {
            kind: RunnerKind::Claude,
            executable: Some(executable.to_string_lossy().into_owned()),
        };
        let settings = DesktopSettings::default();
        assert!(!desktop_setup_complete(&settings, Some(&configured)));
        fs::write(&executable, b"test-cli").unwrap();
        assert!(!desktop_setup_complete(&settings, Some(&configured)));
        let mut saved = DesktopSettings {
            runner_configured: true,
            ..settings
        };
        assert!(!desktop_setup_complete(&saved, Some(&configured)));
        saved.setup_complete = true;
        assert!(desktop_setup_complete(&saved, Some(&configured)));
        fs::remove_file(executable).unwrap();
        assert!(!desktop_setup_complete(&saved, Some(&configured)));
    }

    #[test]
    fn node_versions_are_read_by_their_major() {
        assert_eq!(node_major("v22.12.0\n"), Some(22));
        assert_eq!(node_major("v8.17.0"), Some(8));
        assert_eq!(node_major("22.1.0"), None);
        assert_eq!(node_major(""), None);
    }

    #[cfg(unix)]
    #[test]
    fn the_first_recent_enough_node_is_chosen_and_an_old_one_is_named() {
        use std::os::unix::fs::PermissionsExt;
        let root = tempfile::tempdir().unwrap();
        let node = |version: &str| {
            let directory = root.path().join(version);
            fs::create_dir_all(&directory).unwrap();
            let file = directory.join("node");
            fs::write(&file, format!("#!/bin/sh\necho v{version}\n")).unwrap();
            fs::set_permissions(&file, fs::Permissions::from_mode(0o755)).unwrap();
            directory
        };
        let (old, recent) = (node("20.11.1"), node("22.12.0"));
        let missing = root.path().join("missing");
        assert_eq!(super::first_node([missing.clone(), old.clone(), recent.clone()]).unwrap(), recent.join("node"));
        let error = super::first_node([missing, old]).unwrap_err();
        assert!(error.contains("v20.11.1") && error.contains("https://nodejs.org/"), "{error}");
        assert!(super::first_node([]).unwrap_err().contains("未在本机找到"));
    }

    #[test]
    fn nvm_settings_yield_node_directories() {
        let temporary = tempfile::tempdir().unwrap();
        let node_dir = temporary.path().join("nodejs");
        let settings = temporary.path().join("settings.txt");
        fs::write(&settings, format!("root: ignored\npath: {}\n", node_dir.display())).unwrap();
        assert_eq!(nvm_node_dirs(&settings), vec![node_dir]);
    }
}
