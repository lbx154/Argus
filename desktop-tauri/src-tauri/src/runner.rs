use crate::models::{DesktopSettings, PiConfiguration, RunnerKind, RUNNER_KINDS};
use crate::settings::runner_bin;
use serde_json::Value;
use std::{
    collections::BTreeMap,
    env, fs,
    ffi::OsString,
    path::{Path, PathBuf},
};

/// The oldest Node.js the core package supports (including its minor).
pub const NODE_MINIMUM: &str = "22.12.0";
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
        local_app_data.join("Programs").join("nodejs"),
        local_app_data.join("Volta").join("bin"),
        local_app_data.join("nvs").join("default"),
        home.join("scoop").join("apps").join("nodejs").join("current"),
        home.join("scoop").join("apps").join("nodejs-lts").join("current"),
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
fn supported_node(version: &str) -> bool {
    version.trim().strip_prefix('v').and_then(|version| semver::Version::parse(version).ok())
        .is_some_and(|version| version >= semver::Version::parse(NODE_MINIMUM).expect("valid Node minimum"))
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

/// The first supported Node.js in `directories`, or why there is none.
fn first_node(directories: impl IntoIterator<Item = PathBuf>) -> Result<PathBuf, String> {
    let name = if cfg!(windows) { "node.exe" } else { "node" };
    let mut older = None;
    for node in directories.into_iter().map(|directory| directory.join(name)).filter(|node| node.is_file()) {
        let Some(version) = node_version(&node) else { continue };
        if supported_node(&version) {
            return Ok(node);
        }
        older.get_or_insert(format!("{}（{version}）", node.display()));
    }
    let found = match older {
        Some(older) => format!("本机的 Node.js 版本过低：{older}。"),
        None => "未在本机找到 Node.js。".to_owned(),
    };
    Err(format!(
        "{found}本地后端需要 Node.js {NODE_MINIMUM} 或更高版本。请从 {NODE_DOWNLOAD} 下载安装 LTS 版本\
         （Codex、Claude Code、Copilot CLI 和 Pi 也都通过 npm 安装，同样需要它），安装后点击“重试”。"
    ))
}

/// The Node.js to run Argus with: beside the chosen CLI first (npm puts CLIs
/// next to Node with nvm, Volta and Homebrew), then PATH, then install_dirs.
pub fn find_node(runner: Option<&Path>) -> Result<PathBuf, String> {
    let beside = runner.and_then(Path::parent).map(Path::to_path_buf);
    first_node(beside.into_iter().chain(path_dirs()).chain(install_dirs()))
}

pub fn resolve_runner_binary(kind: &RunnerKind) -> Option<String> {
    let name = kind.as_str();
    let names = [format!("{name}.cmd"), format!("{name}.exe"), name.to_owned()];
    let home = home_dir();
    let local_app_data = local_app_data_dir();
    let mut candidates = vec![app_data_dir().join("npm"), home.join(".local").join("bin")];
    match kind {
        RunnerKind::Codex => candidates.push(local_app_data.join("Microsoft").join("WindowsApps")),
        RunnerKind::Claude => candidates.push(local_app_data.join("Programs").join("claude-code")),
        RunnerKind::Copilot => {
            candidates.push(local_app_data.join("Programs").join("github-copilot-cli"))
        }
        RunnerKind::Pi => {}
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

/// npm's Windows shim is a shell script, not an executable. Run its quoted
/// dp0 target directly, just as the core does, so cmd.exe never reparses paths
/// or CLI arguments. Unknown batch wrappers are reported, not run through a shell.
pub fn runner_argv(_node: &Path, runner: &Path) -> Result<Vec<OsString>, String> {
    #[cfg(not(windows))]
    return Ok(vec![runner.as_os_str().to_owned()]);
    #[cfg(windows)]
    {
        let mut target = runner.to_path_buf();
        let mut prefix = Vec::new();
        if runner.extension().is_some_and(|ext| ext.eq_ignore_ascii_case("cmd") || ext.eq_ignore_ascii_case("bat")) {
            let contents = fs::read_to_string(runner).map_err(|error| format!("无法读取 Agent CLI shim：{error}"))?;
            let (file, args) = shim_target(&contents).ok_or_else(|| format!("不支持的 Agent CLI 批处理包装：{}；请选择 npm shim 或原生可执行文件。", runner.display()))?;
            target = runner.parent().unwrap_or_else(|| Path::new(".")).join(file);
            prefix = args;
        }
        if !target.is_file() { return Err(format!("Agent CLI shim 的目标不存在：{}", target.display())); }
        let native = target.extension().is_some_and(|ext| ext.eq_ignore_ascii_case("exe") || ext.eq_ignore_ascii_case("com"));
        let script = !native && (target.extension().is_some_and(|ext| ["js", "mjs", "cjs"].iter().any(|known| ext.eq_ignore_ascii_case(known)))
            || fs::read(&target).ok().is_some_and(|bytes| {
                regex::Regex::new(r"^#![^\r\n]*\bnode(?:\s|$)").unwrap().is_match(&String::from_utf8_lossy(&bytes[..bytes.len().min(256)]))
            }));
        let mut argv = if script { vec![_node.as_os_str().to_owned(), target.into_os_string()] } else { vec![target.into_os_string()] };
        argv.extend(prefix);
        Ok(argv)
    }
}

#[cfg(windows)]
fn shim_target(source: &str) -> Option<(String, Vec<OsString>)> {
    // Same forwarding grammar as core commands.ts; the shared JSON fixture
    // below keeps both languages honest, including fixed literal arguments.
    static SHIM: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let expression = SHIM.get_or_init(|| regex::Regex::new(r#"(?im)^[ \t]*@?(?:endLocal[ \t]+&[ \t]+goto[ \t]+#_undefined_#[ \t]+2>NUL[ \t]+\|\|[ \t]+title[ \t]+%COMSPEC%[ \t]+&[ \t]+(?:set[ \t]+PATHEXT=%PATHEXT:;\.JS;=;%[ \t]+&[ \t]+)?)?(?:(?:"(?:%_prog%|(?:[^"\r\n]*[\\/])?node(?:\.exe)?)"|node(?:\.exe)?)[ \t]+)?"%(?:~dp0|dp0%)[\\/]*([^"\r\n]+)"((?:[ \t]+[A-Za-z0-9_-]+)*)[ \t]+%\*[ \t]*\r?$"#).unwrap());
    let matched = expression.captures(source)?;
    Some((matched[1].to_owned(), matched[2].split_ascii_whitespace().map(OsString::from).collect()))
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
        desktop_setup_complete, supported_node, nvm_node_dirs, resolve_runner_configuration,
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
    fn node_versions_enforce_the_core_minimum_including_minor() {
        for version in ["v22.12.0\n", "v22.12.1", "v24.0.0"] {
            assert!(supported_node(version), "{version}");
        }
        for version in ["v22.0.0", "v22.11.9", "v20.19.0", "22.12.0", "v22", "", "v22.12.0-rc.1"] {
            assert!(!supported_node(version), "{version}");
        }
    }

    #[cfg(windows)]
    #[test]
    fn npm_shims_resolve_the_real_quoted_target_without_a_shell() {
        let root = tempfile::tempdir().unwrap();
        let directory = root.path().join("中文 & spaces %PATH%");
        fs::create_dir(&directory).unwrap();
        let shim = directory.join("pi.cmd");
        let target = directory.join("node_modules/cli.js");
        fs::create_dir_all(target.parent().unwrap()).unwrap();
        fs::write(&target, b"console.log('fixture')").unwrap();
        // Captured from npm's installed Pi shim, with its package path trimmed.
        fs::write(&shim, include_str!("../test-fixtures/npm-cli.cmd")).unwrap();
        let node = super::find_node(None).unwrap();
        assert_eq!(super::runner_argv(&node, &shim).unwrap(), vec![node.into_os_string(), directory.join(r"node_modules\cli.js").into_os_string()]);
        fs::remove_file(&target).unwrap();
        assert!(super::runner_argv(std::path::Path::new("node.exe"), &shim).unwrap_err().contains("目标不存在"));
        fs::write(&shim, b"@echo off\necho unsupported\n").unwrap();
        assert!(super::runner_argv(std::path::Path::new("node.exe"), &shim).unwrap_err().contains("不支持"));
    }

    #[cfg(windows)]
    #[test]
    fn shim_grammar_matches_the_core_fixture() {
        let fixtures: serde_json::Value = serde_json::from_str(include_str!("../../../packages/core/test/fixtures/windows-shims.json")).unwrap();
        for fixture in fixtures.as_array().unwrap() {
            let result = super::shim_target(fixture["source"].as_str().unwrap());
            if fixture["target"].is_null() {
                assert!(result.is_none(), "{}", fixture["name"]);
            } else {
                let (target, prefix) = result.unwrap_or_else(|| panic!("{}", fixture["name"]));
                assert_eq!(target, fixture["target"].as_str().unwrap());
                let args: Vec<super::OsString> = fixture["prefix"].as_array().unwrap().iter().map(|arg| super::OsString::from(arg.as_str().unwrap())).collect();
                assert_eq!(prefix, args, "{}", fixture["name"]);
            }
        }
    }

    #[test]
    fn desktop_node_minimum_matches_the_shipped_core_package() {
        let package: serde_json::Value = serde_json::from_str(include_str!("../../../packages/core/package.json")).unwrap();
        assert_eq!(package["engines"]["node"], format!(">={}", super::NODE_MINIMUM));
    }

    #[cfg(windows)]
    #[test]
    fn native_node_discovery_works_without_a_terminal_path() {
        let root = tempfile::tempdir().unwrap();
        let directory = root.path().join("中文 Program Files/nodejs");
        fs::create_dir_all(&directory).unwrap();
        fs::copy(super::find_node(None).unwrap(), directory.join("node.exe")).unwrap();
        let exe = std::env::current_exe().unwrap();
        for mode in ["beside", "Path", "install"] {
            let mut command = std::process::Command::new(&exe);
            command.args(["--exact", "runner::tests::node_discovery_child", "--ignored"])
                .env_clear().env("SystemRoot", std::env::var_os("SystemRoot").unwrap())
                .env("ARGUS_NODE_TEST_DIR", &directory).env("ARGUS_NODE_TEST_MODE", mode);
            if mode == "Path" { command.env("Path", &directory); }
            if mode == "install" { command.env("ProgramFiles", directory.parent().unwrap()); }
            let output = command.output().unwrap();
            assert!(output.status.success(), "{mode}: {} {}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        }
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "isolated environment for native_node_discovery_works_without_a_terminal_path"]
    fn node_discovery_child() {
        let directory = std::path::PathBuf::from(std::env::var_os("ARGUS_NODE_TEST_DIR").unwrap());
        let runner = directory.join("pi.cmd");
        let beside = std::env::var("ARGUS_NODE_TEST_MODE").unwrap() == "beside";
        let found = super::find_node(beside.then_some(runner.as_path())).unwrap();
        assert_eq!(found, directory.join("node.exe"));
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
