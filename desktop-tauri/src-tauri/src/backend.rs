// The desktop's Argus: the TypeScript core it ships, run with the system's
// Node.js as a child process (`node argus.mjs --port P --backend B`). Argus
// says where it listens on its first line, `Argus is at <url>`; that line is
// the only readiness signal, and the child's exit is the only failure signal.
// The desktop gives it the Web token (ARGUS_WEB_TOKEN) and its state root
// (ARGUS_HOME), and stops it when asked or when the desktop quits.

use crate::{
    logger::DesktopLogger,
    models::{BackendState, BackendStatus, DesktopSettings},
    process::terminate_process_tree,
    redaction::redact_sensitive_text,
    release::ReleaseContext,
    runner::{argus_home_dir, desktop_setup_complete, find_node, resolve_runner_configuration, NODE_MAJOR},
    settings::SettingsStore,
};
use reqwest::Client;
use serde::Deserialize;
use std::{
    collections::VecDeque,
    env,
    ffi::OsString,
    fs,
    path::{Path, PathBuf},
    process::Stdio,
    sync::{Arc, Mutex},
    time::Duration,
};
use tauri::{AppHandle, Emitter};
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    process::Command,
    time::timeout,
};

const READY_PREFIX: &str = "Argus is at ";
const PRIOR_PROBE_TIMEOUT: Duration = Duration::from_secs(2);
const RUNNER_PREFLIGHT_TIMEOUT: Duration = Duration::from_secs(8);
// A GUI host must not flash a console window for its child processes.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub struct BackendSupervisor {
    app: AppHandle,
    settings: Arc<SettingsStore>,
    release: ReleaseContext,
    logger: DesktopLogger,
    client: Client,
    runtime: Mutex<Runtime>,
}

#[derive(Default)]
struct Runtime {
    status: BackendStatus,
    /// The Argus process this desktop started, while it runs.
    pid: Option<u32>,
    /// Where it listens, from its first line.
    origin: Option<String>,
    /// Each start and stop begins a new generation, so a process's late
    /// output or exit never changes the status of a newer one.
    generation: u64,
    log_tail: VecDeque<String>,
}

/// The fields of `/api/meta` that identify a running Argus to its token holder.
#[derive(Deserialize)]
struct Meta {
    authentication: MetaAuthentication,
    runtime: MetaRuntime,
}

#[derive(Deserialize)]
struct MetaAuthentication {
    authenticated: bool,
}

#[derive(Deserialize)]
struct MetaRuntime {
    pid: u32,
}

impl BackendSupervisor {
    pub fn new(
        app: AppHandle,
        settings: Arc<SettingsStore>,
        release: ReleaseContext,
        logger: DesktopLogger,
    ) -> anyhow::Result<Arc<Self>> {
        // Loopback requests never go through a host proxy.
        let client = Client::builder().no_proxy().build()?;
        Ok(Arc::new(Self { app, settings, release, logger, client, runtime: Mutex::default() }))
    }

    fn runtime(&self) -> std::sync::MutexGuard<'_, Runtime> {
        self.runtime.lock().expect("runtime mutex poisoned")
    }

    pub fn current_status(&self) -> BackendStatus {
        let settings = self.settings.snapshot();
        let runtime = self.runtime();
        let mut status = runtime.status.clone();
        status.pid = runtime.pid;
        if status.state == BackendState::Ready {
            status.url = runtime.origin.as_deref().map(|origin| SettingsStore::cockpit_url(origin, &settings));
        }
        status
    }

    /// The origin the running Argus serves, which alone may hand the window a download.
    pub fn origin(&self) -> Option<String> {
        self.runtime().origin.clone()
    }

    pub fn release(&self) -> &ReleaseContext {
        &self.release
    }

    fn set_status(&self, state: BackendState, message: impl Into<String>, detail: Option<String>) {
        let message = message.into();
        self.logger.info(format!("backend {state:?}: {message} {}", detail.as_deref().unwrap_or_default()));
        self.runtime().status = BackendStatus { state, message, detail, pid: None, url: None };
        let _ = self.app.emit("argus:status", self.current_status());
    }

    fn is_current(&self, generation: u64) -> bool {
        self.runtime().generation == generation
    }

    pub async fn start(self: &Arc<Self>) {
        let generation = {
            let mut runtime = self.runtime();
            if matches!(runtime.status.state, BackendState::Starting | BackendState::Ready) {
                return;
            }
            runtime.generation += 1;
            runtime.generation
        };
        let settings = self.settings.snapshot();
        let configured = resolve_runner_configuration(&settings);
        if !desktop_setup_complete(&settings, configured.as_ref()) {
            return self.set_status(BackendState::Idle, "请选择并确认 Agent CLI 后开始使用", None);
        }
        let runner = configured.and_then(|runner| runner.executable).map(PathBuf::from);
        let script = match self.release.validate_payload() {
            Ok(script) => script,
            Err(detail) => return self.set_status(BackendState::Error, "本地后端文件需要修复", Some(detail)),
        };
        let node = match find_node(runner.as_deref()) {
            Ok(node) => node,
            Err(detail) => return self.set_status(
                BackendState::Error, format!("需要 Node.js {NODE_MAJOR} 或更高版本"), Some(detail),
            ),
        };
        self.set_status(BackendState::Starting, "正在启动本地后端", None);
        self.stop_prior(&settings).await;
        if !self.is_current(generation) {
            return;
        }
        if let Err(error) = self.spawn(generation, &node, &script, runner.as_deref(), &settings) {
            self.set_status(BackendState::Error, "无法启动本地后端", Some(format!("{error:#}")));
        }
    }

    pub async fn restart(self: &Arc<Self>) {
        self.stop().await;
        self.start().await;
    }

    pub async fn stop(&self) {
        let pid = {
            let mut runtime = self.runtime();
            runtime.generation += 1;
            runtime.origin = None;
            runtime.pid.take()
        };
        if let Some(pid) = pid {
            terminate_process_tree(pid).await;
        }
        self.set_status(BackendState::Stopped, "已停止", None);
    }

    /// An Argus this desktop started and lost (the desktop crashed or was
    /// killed) still holds the port. It answers `/api/meta` as authenticated
    /// only to this desktop's token, which proves it is ours to stop.
    async fn stop_prior(&self, settings: &DesktopSettings) {
        let request = self.client.get(format!("http://127.0.0.1:{}/api/meta", settings.port))
            .bearer_auth(&settings.token)
            .timeout(PRIOR_PROBE_TIMEOUT);
        let Ok(response) = request.send().await else { return };
        let Ok(meta) = response.json::<Meta>().await else { return };
        if meta.authentication.authenticated {
            self.logger.info(format!("stopping the Argus this desktop left running (pid {})", meta.runtime.pid));
            terminate_process_tree(meta.runtime.pid).await;
        }
    }

    fn spawn(
        self: &Arc<Self>,
        generation: u64,
        node: &Path,
        script: &Path,
        runner: Option<&Path>,
        settings: &DesktopSettings,
    ) -> anyhow::Result<()> {
        self.logger.info(format!("starting {} {} with {}", node.display(), script.display(), settings.runner_kind.label()));
        let mut child = argus_command(node, script, runner, settings, &argus_home_dir())?.spawn()?;
        {
            let mut runtime = self.runtime();
            if runtime.generation != generation {
                return Ok(()); // stopped meanwhile: dropping the child kills it
            }
            runtime.pid = child.id();
            runtime.origin = None;
            runtime.log_tail.clear();
        }
        if let Some(stdout) = child.stdout.take() {
            let supervisor = Arc::clone(self);
            tokio::spawn(async move {
                let mut lines = BufReader::new(stdout).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    if let Some(origin) = ready_origin(&line) {
                        supervisor.ready(generation, origin);
                    }
                    supervisor.append_log(&line);
                }
            });
        }
        if let Some(stderr) = child.stderr.take() {
            let supervisor = Arc::clone(self);
            tokio::spawn(async move {
                let mut lines = BufReader::new(stderr).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    supervisor.append_log(&line);
                }
            });
        }
        let supervisor = Arc::clone(self);
        tokio::spawn(async move {
            let code = child.wait().await.ok().and_then(|status| status.code());
            supervisor.exited(generation, code);
        });
        Ok(())
    }

    fn ready(&self, generation: u64, origin: String) {
        {
            let mut runtime = self.runtime();
            if runtime.generation != generation || runtime.status.state != BackendState::Starting {
                return;
            }
            runtime.origin = Some(origin);
        }
        self.set_status(BackendState::Ready, "本地后端已就绪", None);
    }

    fn exited(&self, generation: u64, code: Option<i32>) {
        let was_ready = {
            let mut runtime = self.runtime();
            if runtime.generation != generation {
                return;
            }
            runtime.pid = None;
            runtime.origin = None;
            runtime.status.state == BackendState::Ready
        };
        let code = code.map_or_else(|| "none".to_owned(), |code| code.to_string());
        self.set_status(
            BackendState::Error,
            if was_ready { "本地后端意外退出" } else { "本地后端启动失败" },
            Some(format!("exit code: {code}\n{}", self.log_tail())),
        );
    }

    /// Check the chosen CLI starts (`--version`, no model turn) with the
    /// PATH Argus will give it, so a CLI that cannot run is seen at setup.
    pub async fn validate_runner(&self, runner: &str) -> Result<(), String> {
        let runner = Path::new(runner);
        let path = runtime_path(&find_node(Some(runner))?, Some(runner)).map_err(|error| error.to_string())?;
        let mut command = Command::new(runner);
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        command
            .arg("--version")
            .env("PATH", path)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        let result = match timeout(RUNNER_PREFLIGHT_TIMEOUT, command.output()).await {
            Ok(Ok(output)) if output.status.success() => Ok(()),
            Ok(Ok(output)) => {
                let stderr = redact_sensitive_text(&String::from_utf8_lossy(&output.stderr));
                let detail = stderr.trim();
                Err(format!(
                    "Agent CLI 无法运行（exit {}）：{}",
                    output.status.code().map_or_else(|| "none".to_owned(), |code| code.to_string()),
                    if detail.is_empty() { "no stderr" } else { detail },
                ))
            }
            Ok(Err(error)) => Err(format!("无法启动 Agent CLI：{error}")),
            Err(_) => Err(format!(
                "Agent CLI 启动检查超时（{}ms），请在终端确认 CLI 可运行后重试。",
                RUNNER_PREFLIGHT_TIMEOUT.as_millis(),
            )),
        };
        match &result {
            Ok(()) => self.logger.info(format!("runner preflight passed: {}", runner.display())),
            Err(error) => self.logger.warn(error),
        }
        result
    }

    fn append_log(&self, line: &str) {
        let safe = redact_sensitive_text(line);
        {
            let mut runtime = self.runtime();
            runtime.log_tail.push_back(safe.clone());
            while runtime.log_tail.len() > 200 {
                runtime.log_tail.pop_front();
            }
        }
        self.logger.verbose(format!("backend output: {safe}"));
    }

    fn log_tail(&self) -> String {
        let runtime = self.runtime();
        let skip = runtime.log_tail.len().saturating_sub(8);
        runtime.log_tail.iter().skip(skip).cloned().collect::<Vec<_>>().join("\n")
    }
}

/// `node argus.mjs --port P --backend B`, with the Web token and its state root.
fn argus_command(
    node: &Path,
    script: &Path,
    runner: Option<&Path>,
    settings: &DesktopSettings,
    home: &Path,
) -> anyhow::Result<Command> {
    fs::create_dir_all(home)?;
    let mut command = Command::new(node);
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    command
        .arg(script)
        .args(["--port", &settings.port.to_string(), "--backend", settings.runner_kind.as_str()])
        .current_dir(home)
        .env("ARGUS_WEB_TOKEN", &settings.token)
        .env("ARGUS_HOME", home)
        .env("PATH", runtime_path(node, runner)?)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    Ok(command)
}

/// Where Argus listens, from the line it prints once it does.
fn ready_origin(line: &str) -> Option<String> {
    let url = url::Url::parse(line.trim().strip_prefix(READY_PREFIX)?).ok()?;
    (url.scheme() == "http").then(|| url.origin().ascii_serialization())
}

/// PATH for Argus and the CLIs it starts: Node's directory (an npm-installed
/// CLI is a Node script) and the chosen CLI's (Argus finds it by name) first.
fn runtime_path(node: &Path, runner: Option<&Path>) -> anyhow::Result<OsString> {
    let current = env::var_os("PATH").unwrap_or_default();
    let first = [Some(node), runner].into_iter().flatten().filter_map(Path::parent).map(Path::to_path_buf);
    Ok(env::join_paths(first.chain(env::split_paths(&current)))?)
}

/// A download the window may accept: a blob of the page Argus serves.
pub fn is_artifact_download(origin: Option<&str>, url: &url::Url) -> bool {
    url.scheme() == "blob" && origin.is_some_and(|origin| url.origin().ascii_serialization() == origin)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn readiness_is_the_line_argus_prints_once_it_listens() {
        assert_eq!(
            ready_origin("Argus is at http://127.0.0.1:8799/?token=secret\n").as_deref(),
            Some("http://127.0.0.1:8799"),
        );
        assert_eq!(ready_origin("Argus is at http://127.0.0.1:41234/").as_deref(), Some("http://127.0.0.1:41234"));
        for other in ["", "listening", "Argus is at", "Argus is at not a url", "warning: Argus is at http://x/"] {
            assert_eq!(ready_origin(other), None, "{other}");
        }
    }

    #[test]
    fn artifact_downloads_accept_only_the_running_argus_blobs() {
        let origin = Some("http://127.0.0.1:55418");
        let accepted = url::Url::parse("blob:http://127.0.0.1:55418/artifact").unwrap();
        assert!(is_artifact_download(origin, &accepted));
        assert!(!is_artifact_download(None, &accepted));
        for denied in [
            "blob:http://127.0.0.1:9999/artifact",
            "blob:https://example.com/artifact",
            "blob:null/artifact",
            "http://127.0.0.1:55418/api/projects",
            "file:///example.txt",
        ] {
            assert!(!is_artifact_download(origin, &url::Url::parse(denied).unwrap()), "{denied}");
        }
    }

    /// The Argus the build copied beside the test binary, as Tauri places
    /// resources beside a development executable: the package's layout.
    fn shipped_argus() -> PathBuf {
        let target = env::current_exe().unwrap().parent().and_then(Path::parent).unwrap().to_path_buf();
        target.join("argus").join("dist").join("argus.mjs")
    }

    #[tokio::test]
    async fn the_shipped_argus_runs_with_node_and_says_where_it_listens() {
        let node = find_node(None).expect("Node.js 22 or newer for the desktop's tests");
        let home = tempfile::tempdir().unwrap();
        let settings = DesktopSettings { port: 0, token: "desktop-test-token".into(), ..Default::default() };
        let mut child = argus_command(&node, &shipped_argus(), None, &settings, home.path()).unwrap().spawn().unwrap();
        let mut lines = BufReader::new(child.stdout.take().unwrap()).lines();
        let origin = timeout(Duration::from_secs(30), async {
            loop {
                let line = lines.next_line().await.unwrap().expect("Argus exited before it listened");
                if let Some(origin) = ready_origin(&line) {
                    break origin;
                }
            }
        }).await.expect("Argus said where it listens");
        let client = Client::builder().no_proxy().build().unwrap();
        let meta: Meta = client.get(format!("{origin}/api/meta")).bearer_auth(&settings.token)
            .send().await.unwrap().json().await.unwrap();
        assert!(meta.authentication.authenticated, "the token reached Argus");
        assert_eq!(Some(meta.runtime.pid), child.id());
        assert!(home.path().join("owner.json").is_file(), "ARGUS_HOME is its state root");
        let page = client.get(format!("{origin}/")).send().await.unwrap();
        assert!(page.status().is_success(), "the web cockpit is beside it");
        // As the supervisor does: the child is reaped while it is stopped.
        let exited = tokio::spawn(async move { child.wait().await.unwrap() });
        assert!(terminate_process_tree(meta.runtime.pid).await);
        if cfg!(unix) {
            assert!(exited.await.unwrap().success(), "SIGTERM is a clean stop");
            assert!(!home.path().join("owner.json").exists(), "and gives the state root back");
        }
    }

    #[test]
    fn node_and_the_cli_come_first_on_path() {
        let node = Path::new("/opt/node/bin/node");
        let runner = Path::new("/home/me/.npm/bin/codex");
        let path = runtime_path(node, Some(runner)).unwrap();
        let entries: Vec<PathBuf> = env::split_paths(&path).collect();
        assert_eq!(entries[..2], [PathBuf::from("/opt/node/bin"), PathBuf::from("/home/me/.npm/bin")]);
    }
}
