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
    runner::{argus_home_dir, desktop_setup_complete, find_node, resolve_runner_configuration, runner_argv, NODE_MINIMUM},
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
    #[cfg(windows)]
    changed: tokio::sync::Notify,
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
    /// Retain the exit facts so late pipe data can refine that same error,
    /// without holding the root's exit event hostage to descendant pipe EOF.
    exit: Option<Exit>,
}

#[derive(Clone, Copy)]
struct Exit { code: Option<i32>, port: u16, was_ready: bool }

impl Runtime {
    fn begin_start(&mut self) -> Option<u64> {
        if self.pid.is_some() || matches!(self.status.state, BackendState::Starting | BackendState::Ready) { return None; }
        self.generation += 1;
        self.exit = None;
        self.status = BackendStatus { state: BackendState::Starting, message: "正在启动本地后端".into(), ..Default::default() };
        Some(self.generation)
    }

    fn status(&mut self, generation: u64, state: BackendState, message: impl Into<String>, detail: Option<String>) -> bool {
        if self.generation != generation { return false; }
        self.status = BackendStatus { state, message: message.into(), detail, pid: None, url: None };
        true
    }

    fn begin_stop(&mut self) -> (u64, Option<u32>) {
        self.generation += 1;
        self.origin = None;
        (self.generation, self.pid)
    }

    fn stopped(&mut self, generation: u64, result: Result<(), String>) -> bool {
        if self.generation != generation { return false; }
        if cfg!(not(windows)) {
            // Preserve POSIX's existing best-effort stop status semantics.
            self.pid = None;
            return self.status(generation, BackendState::Stopped, "已停止", None);
        }
        match result {
            Ok(()) => { self.pid = None; self.status(generation, BackendState::Stopped, "已停止", None) }
            Err(error) => self.status(generation, BackendState::Error, "无法停止本地后端", Some(format!("{error}\n未启动第二个后端。"))),
        }
    }

    fn ready(&mut self, generation: u64, origin: String) -> bool {
        if self.generation != generation || self.status.state != BackendState::Starting { return false; }
        self.origin = Some(origin);
        self.status(generation, BackendState::Ready, "本地后端已就绪", None)
    }

    fn exited(&mut self, generation: u64, code: Option<i32>, port: u16) -> bool {
        if self.generation != generation { return false; }
        self.exit = Some(Exit { code, port, was_ready: self.status.state == BackendState::Ready });
        self.pid = None;
        self.origin = None;
        self.exit_status(generation)
    }

    fn record_line(&mut self, generation: u64, line: String) -> bool {
        if self.generation != generation { return false; }
        self.log_tail.push_back(line);
        while self.log_tail.len() > 200 { self.log_tail.pop_front(); }
        cfg!(windows) && self.exit.is_some() && self.exit_status(generation)
    }

    fn exit_status(&mut self, generation: u64) -> bool {
        let Some(Exit { code, port, was_ready }) = self.exit else { return false };
        let code = code.map_or_else(|| "none".to_owned(), |code| code.to_string());
        let tail = self.log_tail.iter().skip(self.log_tail.len().saturating_sub(8)).cloned().collect::<Vec<_>>().join("\n");
        let message = if cfg!(windows) && tail.contains("EADDRINUSE") {
            format!("端口 {port} 已被占用，请更换端口或关闭占用它的程序。")
        } else if was_ready { "本地后端意外退出".into() } else { "本地后端启动失败".into() };
        self.status(generation, BackendState::Error, message, Some(format!("exit code: {code}\n{tail}")))
    }
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
    state_root: Option<PathBuf>,
}

/// The authenticated service must also own this state root. A token copied
/// with desktop settings is not authority over a different installation.
fn prior_owns_home(meta: &Meta, home: &Path) -> bool {
    if !meta.authentication.authenticated || meta.runtime.pid == 0 { return false; }
    let roots_match = meta.runtime.state_root.as_deref().and_then(|root| fs::canonicalize(root).ok())
        .zip(fs::canonicalize(home).ok()).is_some_and(|(reported, expected)| reported == expected);
    let owner = fs::read(home.join("owner.json")).ok()
        .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok());
    roots_match && owner.as_ref().and_then(|owner| owner["pid"].as_u64()) == Some(meta.runtime.pid.into())
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
        Ok(Arc::new(Self { app, settings, release, logger, client, runtime: Mutex::default(),
            #[cfg(windows)] changed: tokio::sync::Notify::new(),
        }))
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

    /// Generation validation, mutation and the emitted snapshot share one
    /// critical section. A late callback cannot publish over a newer stop/start.
    fn update(&self, change: impl FnOnce(&mut Runtime) -> bool) {
        let settings = self.settings.snapshot();
        let mut runtime = self.runtime();
        if !change(&mut runtime) { return; }
        let mut status = runtime.status.clone();
        status.pid = runtime.pid;
        if status.state == BackendState::Ready {
            status.url = runtime.origin.as_deref().map(|origin| SettingsStore::cockpit_url(origin, &settings));
        }
        let log = format!("backend {:?}: {} {}", status.state, status.message, status.detail.as_deref().unwrap_or_default());
        if status.state == BackendState::Error { self.logger.error(log); } else { self.logger.info(log); }
        let _ = self.app.emit("argus:status", status);
        #[cfg(windows)] self.changed.notify_waiters();
    }

    fn set_status(&self, generation: u64, state: BackendState, message: impl Into<String>, detail: Option<String>) {
        self.update(|runtime| runtime.status(generation, state, message, detail));
    }

    /// Windows setup uses the child's readiness/exit events, not a status poll.
    #[cfg(windows)]
    pub async fn started(&self) -> BackendStatus {
        loop {
            let changed = self.changed.notified();
            tokio::pin!(changed);
            changed.as_mut().enable();
            let status = self.current_status();
            if status.state != BackendState::Starting { return status; }
            changed.await;
        }
    }

    fn is_current(&self, generation: u64) -> bool {
        self.runtime().generation == generation
    }

    pub async fn start(self: &Arc<Self>) {
        if crate::state(&self.app).quitting.load(std::sync::atomic::Ordering::SeqCst) { return; }
        let generation = {
            let mut generation = None;
            self.update(|runtime| { generation = runtime.begin_start(); generation.is_some() });
            let Some(generation) = generation else { return };
            generation
        };
        let settings = self.settings.snapshot();
        let configured = resolve_runner_configuration(&settings);
        if !desktop_setup_complete(&settings, configured.as_ref()) {
            return self.set_status(generation, BackendState::Idle, "请选择并确认 Agent CLI 后开始使用", None);
        }
        let runner = configured.and_then(|runner| runner.executable).map(PathBuf::from);
        let script = match self.release.validate_payload() {
            Ok(script) => script,
            Err(detail) => return self.set_status(generation, BackendState::Error, "本地后端文件需要修复", Some(detail)),
        };
        let node = match find_node(runner.as_deref()) {
            Ok(node) => node,
            Err(detail) => return self.set_status(
                generation, BackendState::Error, format!("需要 Node.js {NODE_MINIMUM} 或更高版本"), Some(detail),
            ),
        };
        if !self.is_current(generation) { return; }
        if let Err(detail) = self.stop_prior(generation, &settings).await {
            return self.set_status(generation, BackendState::Error, "无法接管旧的本地后端", Some(detail));
        }
        if !self.is_current(generation) {
            return;
        }
        if let Err(error) = self.spawn(generation, &node, &script, runner.as_deref(), &settings) {
            self.set_status(generation, BackendState::Error, "无法启动本地后端", Some(format!("{error:#}")));
        }
    }

    pub async fn restart(self: &Arc<Self>) {
        let stopped = self.stop().await;
        if cfg!(windows) && stopped.is_err() { return; }
        self.start().await;
    }

    pub async fn stop(&self) -> Result<(), String> {
        let (generation, pid) = self.runtime().begin_stop();
        let result = if let Some(pid) = pid { terminate_process_tree(pid).await } else { Ok(()) };
        self.update(|runtime| runtime.stopped(generation, result.clone()));
        result
    }

    /// An Argus this desktop started and lost (the desktop crashed or was
    /// killed) still holds the port. It answers `/api/meta` as authenticated
    /// only to this desktop's token, which proves it is ours to stop.
    async fn stop_prior(&self, generation: u64, settings: &DesktopSettings) -> Result<(), String> {
        let request = self.client.get(format!("http://127.0.0.1:{}/api/meta", settings.port))
            .bearer_auth(&settings.token)
            .timeout(PRIOR_PROBE_TIMEOUT);
        let Ok(response) = request.send().await else { return Ok(()) };
        if !response.status().is_success() { return Ok(()); }
        let Ok(meta) = response.json::<Meta>().await else { return Ok(()) };
        if meta.authentication.authenticated {
            if cfg!(windows) && !prior_owns_home(&meta, &argus_home_dir()) {
                return Err("端口上的 Argus 不拥有本桌面的状态根；未停止它。请检查端口和 ARGUS_HOME。".into());
            }
            {
                let mut runtime = self.runtime();
                if runtime.generation != generation { return Ok(()); }
                // A concurrent stop must know this PID too; never lose its
                // identity while an authenticated takeover is underway.
                runtime.pid = Some(meta.runtime.pid);
            }
            self.logger.info(format!("stopping the Argus this desktop left running (pid {})", meta.runtime.pid));
            terminate_process_tree(meta.runtime.pid).await?;
            let mut runtime = self.runtime();
            if runtime.generation == generation { runtime.pid = None; }
        }
        Ok(())
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
        let mut command = argus_command(node, script, runner, settings, &argus_home_dir())?;
        let child = {
            let mut runtime = self.runtime();
            if runtime.generation != generation || crate::state(&self.app).quitting.load(std::sync::atomic::Ordering::SeqCst) { return Ok(()); }
            // Stop cannot interleave between process creation and recording
            // its PID, or it could miss the newly created process tree.
            let child = command.spawn()?;
            runtime.pid = child.id();
            runtime.origin = None;
            runtime.log_tail.clear();
            child
        };
        let (output, errors, supervisor) = (Arc::clone(self), Arc::clone(self), Arc::clone(self));
        let port = settings.port;
        let (mut child, _) = read_child_output(child, move |line| {
            if let Some(origin) = ready_origin(&line) { output.ready(generation, origin); }
            output.append_log(generation, &line);
        }, move |line| errors.append_log(generation, &line));
        tokio::spawn(async move {
            let code = child.wait().await.ok().and_then(|status| status.code());
            // Root exit is authoritative even when a descendant keeps a pipe
            // open. Any late bytes refine this error via record_line.
            supervisor.update(|runtime| runtime.exited(generation, code, port));
        });
        Ok(())
    }

    fn ready(&self, generation: u64, origin: String) {
        self.update(|runtime| runtime.ready(generation, origin));
    }

    /// Check the chosen CLI starts (`--version`, no model turn) with the
    /// PATH Argus will give it, so a CLI that cannot run is seen at setup.
    pub async fn validate_runner(&self, runner: &str) -> Result<(), String> {
        let runner = Path::new(runner);
        let node = find_node(Some(runner))?;
        let path = runtime_path(&node, Some(runner)).map_err(|error| error.to_string())?;
        let argv = runner_argv(&node, runner)?;
        let mut command = Command::new(&argv[0]);
        command.args(&argv[1..]);
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

    fn append_log(&self, generation: u64, line: &str) {
        let safe = redact_sensitive_text(line);
        let mut accepted = false;
        self.update(|runtime| {
            if runtime.generation != generation { return false; }
            accepted = true;
            runtime.record_line(generation, safe.clone())
        });
        if accepted { self.logger.verbose(format!("backend output: {safe}")); }
    }

}

/// Pipe EOF is not the root's lifecycle signal. Readers remain independent;
/// the caller reports child.wait immediately, and late data refines its detail.
fn read_child_output(
    mut child: tokio::process::Child,
    mut output: impl FnMut(String) + Send + 'static,
    mut errors: impl FnMut(String) + Send + 'static,
) -> (tokio::process::Child, Vec<tokio::task::JoinHandle<()>>) {
    let mut readers = Vec::new();
    if let Some(stdout) = child.stdout.take() {
        readers.push(tokio::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await { output(line); }
        }));
    }
    if let Some(stderr) = child.stderr.take() {
        readers.push(tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await { errors(line); }
        }));
    }
    (child, readers)
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
    // Tauri resource paths can have a verbatim Windows prefix. Node's entry
    // module resolver rejects that form, even though the file exists.
    #[cfg(windows)]
    let script = dunce::simplified(script);
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
    #[cfg(windows)]
    {
        // Clear inherited overrides. Export confirmed saved paths by backend,
        // plus the currently detected selection, as absolute native paths.
        for kind in crate::models::RUNNER_KINDS {
            let key = format!("ARGUS_{}_BIN", kind.as_str().to_ascii_uppercase());
            command.env_remove(&key);
            let configured = if kind == settings.runner_kind { runner.map(Path::to_path_buf) }
                else { crate::settings::runner_bin(settings, &kind).map(|requested| {
                    let mut choice = settings.clone(); choice.runner_kind = kind.clone();
                    resolve_runner_configuration(&choice).and_then(|resolved| resolved.executable)
                        .map(PathBuf::from).unwrap_or_else(|| PathBuf::from(requested))
                }) };
            if let Some(path) = configured {
                // Preserve other explicitly saved backend paths too: existing
                // projects retain their own backend when the desktop changes.
                // A missing inactive path stays explicit so core can explain it,
                // instead of silently choosing another installation from PATH.
                let absolute = if path.is_absolute() { path } else { env::current_dir()?.join(path) };
                let path = fs::canonicalize(&absolute).unwrap_or(absolute);
                command.env(key, dunce::simplified(&path));
            }
        }
    }
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

    #[tokio::test]
    async fn stop_during_preflight_cannot_be_overwritten_by_the_old_start() {
        let runtime = Arc::new(Mutex::new(Runtime::default()));
        let entered = Arc::new(tokio::sync::Barrier::new(2));
        let release = Arc::new(tokio::sync::Notify::new());
        let (old, barrier, resume) = (Arc::clone(&runtime), Arc::clone(&entered), Arc::clone(&release));
        let preflight = tokio::spawn(async move {
            let generation = old.lock().unwrap().begin_start().unwrap();
            barrier.wait().await;
            resume.notified().await;
            old.lock().unwrap().status(generation, BackendState::Starting, "late preflight", None)
        });
        entered.wait().await;
        {
            let mut state = runtime.lock().unwrap();
            let (generation, _) = state.begin_stop();
            assert!(state.stopped(generation, Ok(())));
        }
        release.notify_one();
        assert!(!preflight.await.unwrap());
        assert_eq!(runtime.lock().unwrap().status.state, BackendState::Stopped);
    }

    #[tokio::test]
    async fn late_ready_and_exit_cannot_change_a_new_generation() {
        let runtime = Arc::new(Mutex::new(Runtime::default()));
        let entered = Arc::new(tokio::sync::Barrier::new(2));
        let release = Arc::new(tokio::sync::Notify::new());
        let old_generation = runtime.lock().unwrap().begin_start().unwrap();
        let (old, barrier, resume) = (Arc::clone(&runtime), Arc::clone(&entered), Arc::clone(&release));
        let callback = tokio::spawn(async move {
            barrier.wait().await;
            resume.notified().await;
            let mut state = old.lock().unwrap();
            assert!(!state.ready(old_generation, "http://old".into()));
            assert!(!state.exited(old_generation, Some(1), 1234));
        });
        entered.wait().await;
        {
            let mut state = runtime.lock().unwrap();
            let (stopped, _) = state.begin_stop(); state.stopped(stopped, Ok(()));
            let new = state.begin_start().unwrap(); state.pid = Some(54321);
            assert!(state.ready(new, "http://new".into()));
        }
        release.notify_one(); callback.await.unwrap();
        let state = runtime.lock().unwrap();
        assert_eq!(state.status.state, BackendState::Ready);
        assert_eq!(state.pid, Some(54321));
        assert_eq!(state.origin.as_deref(), Some("http://new"));
    }

    #[cfg(windows)]
    #[test]
    fn a_stop_failure_keeps_the_process_identity_and_blocks_a_second_start() {
        let mut runtime = Runtime::default();
        runtime.begin_start(); runtime.pid = Some(54321);
        let (generation, _) = runtime.begin_stop();
        runtime.stopped(generation, Err("system taskkill failed".into()));
        assert_eq!(runtime.pid, Some(54321));
        assert_eq!(runtime.status.state, BackendState::Error);
        assert!(runtime.status.detail.as_deref().unwrap().contains("system taskkill failed"));
        assert!(runtime.begin_start().is_none());
        runtime.stopped(generation, Ok(()));
        assert!(runtime.begin_start().is_some());
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn immediate_stderr_refines_the_exit_diagnostic_even_if_it_arrives_late() {
        let node = find_node(None).unwrap();
        let child = Command::new(node).args(["-e", "process.stderr.write('fixture EADDRINUSE immediate failure\\n'); process.exitCode=7;"])
            .stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
        let runtime = Arc::new(Mutex::new(Runtime::default()));
        let generation = runtime.lock().unwrap().begin_start().unwrap();
        let errors = Arc::clone(&runtime);
        let (mut child, readers) = read_child_output(child, |_| {}, move |line| { errors.lock().unwrap().record_line(generation, line); });
        let code = child.wait().await.unwrap().code();
        runtime.lock().unwrap().exited(generation, code, 4567);
        for reader in readers { reader.await.unwrap(); }
        let state = runtime.lock().unwrap();
        assert_eq!(code, Some(7));
        assert!(state.status.message.contains("端口 4567 已被占用"));
        assert!(state.status.detail.as_deref().unwrap().contains("fixture EADDRINUSE immediate failure"));
    }

    #[cfg(windows)]
    #[test]
    fn error_detail_updates_when_the_exit_event_precedes_a_pipe_line() {
        let mut runtime = Runtime::default();
        let generation = runtime.begin_start().unwrap();
        runtime.exited(generation, Some(7), 4567);
        assert!(runtime.record_line(generation, "late EADDRINUSE fixture".into()));
        assert_eq!(runtime.status.state, BackendState::Error);
        assert!(runtime.status.message.contains("端口 4567 已被占用"));
        let (stopped, _) = runtime.begin_stop(); runtime.stopped(stopped, Ok(()));
        assert!(!runtime.record_line(generation, "old process data".into()));
        assert_eq!(runtime.status.state, BackendState::Stopped);
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn root_exit_is_reported_while_a_descendant_still_holds_both_output_pipes() {
        let node = find_node(None).unwrap();
        let child = Command::new(node).args(["-e", "const c=require('child_process').spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'inherit',detached:true}); c.unref(); console.log(c.pid); process.stderr.write('root immediate failure\\n',()=>process.exit(7));"])
            .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).kill_on_drop(true).spawn().unwrap();
        let runtime = Arc::new(Mutex::new(Runtime::default()));
        let generation = runtime.lock().unwrap().begin_start().unwrap();
        let (tx, rx) = tokio::sync::oneshot::channel();
        let mut send = Some(tx);
        let errors = Arc::clone(&runtime);
        let line_read = Arc::new(tokio::sync::Notify::new());
        let notified = Arc::clone(&line_read);
        let (mut child, readers) = read_child_output(child, move |line| {
            if let Some(send) = send.take() { let _ = send.send(line.parse::<u32>().unwrap()); }
        }, move |line| { errors.lock().unwrap().record_line(generation, line); notified.notify_one(); });
        let descendant = timeout(Duration::from_secs(10), rx).await.unwrap().unwrap();
        let code = timeout(Duration::from_secs(10), child.wait()).await.unwrap().unwrap().code();
        runtime.lock().unwrap().exited(generation, code, 4567);
        line_read.notified().await;
        let descendant_alive = crate::process::is_process_alive(descendant);
        let pipes_still_open = readers.iter().any(|reader| !reader.is_finished());
        let error_was_reported = runtime.lock().unwrap().status.state == BackendState::Error;
        // The fixture, not the product, cleans up the deliberately orphaned PID.
        terminate_process_tree(descendant).await.unwrap();
        for reader in readers { timeout(Duration::from_secs(10), reader).await.unwrap().unwrap(); }
        assert_eq!(code, Some(7));
        assert!(descendant_alive && pipes_still_open && error_was_reported);
        assert!(runtime.lock().unwrap().status.detail.as_deref().unwrap().contains("root immediate failure"));
    }

    #[cfg(windows)]
    #[test]
    fn the_confirmed_custom_cli_path_reaches_the_core_not_only_its_path_directory() {
        let home = tempfile::tempdir().unwrap();
        let runner = home.path().join("自定义 chosen script.mjs");
        fs::write(&runner, b"console.log('fixture')").unwrap();
        let other = home.path().join("configured codex.cmd");
        fs::write(&other, b"fixture").unwrap();
        let settings = DesktopSettings { runner_kind: crate::models::RunnerKind::Pi,
            runner_bins: std::collections::BTreeMap::from([("codex".into(), other.to_string_lossy().into_owned())]),
            runner_configured: true, ..Default::default() };
        let command = argus_command(Path::new("node.exe"), Path::new("argus.mjs"), Some(&runner), &settings, home.path()).unwrap();
        let variables: Vec<_> = command.as_std().get_envs().collect();
        let expected = fs::canonicalize(&runner).unwrap();
        assert_eq!(variables.iter().find(|(name, _)| *name == "ARGUS_PI_BIN").unwrap().1, Some(dunce::simplified(&expected).as_os_str()));
        let other = fs::canonicalize(other).unwrap();
        assert_eq!(variables.iter().find(|(name, _)| *name == "ARGUS_CODEX_BIN").unwrap().1, Some(dunce::simplified(&other).as_os_str()));
        for name in ["ARGUS_CLAUDE_BIN", "ARGUS_COPILOT_BIN"] {
            assert_eq!(variables.iter().find(|(key, _)| *key == name).unwrap().1, None);
        }
        fs::remove_file(other).unwrap();
        let command = argus_command(Path::new("node.exe"), Path::new("argus.mjs"), Some(&runner), &settings, home.path()).unwrap();
        assert!(command.as_std().get_envs().any(|(key, value)| key == "ARGUS_CODEX_BIN" && value.is_some()), "a missing inactive choice stays explicit instead of falling back");
    }

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
    fn lost_backend_requires_authentication_and_the_same_owner_record() {
        let home = tempfile::tempdir().unwrap();
        let other = tempfile::tempdir().unwrap();
        let mut meta = Meta { authentication: MetaAuthentication { authenticated: true },
            runtime: MetaRuntime { pid: 12345, state_root: Some(home.path().to_path_buf()) } };
        assert!(!prior_owns_home(&meta, home.path()));
        fs::write(home.path().join("owner.json"), br#"{"pid":12345,"started":"fixture"}"#).unwrap();
        assert!(prior_owns_home(&meta, home.path()));
        meta.authentication.authenticated = false;
        assert!(!prior_owns_home(&meta, home.path()));
        meta.authentication.authenticated = true;
        meta.runtime.state_root = Some(other.path().to_path_buf());
        assert!(!prior_owns_home(&meta, home.path()));
        meta.runtime.state_root = Some(home.path().to_path_buf());
        meta.runtime.pid = 99999;
        assert!(!prior_owns_home(&meta, home.path()));
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
        target.join("argus-core").join("dist").join("argus.mjs")
    }

    #[tokio::test]
    async fn the_shipped_argus_runs_with_node_and_says_where_it_listens() {
        let node = find_node(None).expect("Node.js 22 or newer for the desktop's tests");
        let home = tempfile::tempdir().unwrap();
        let settings = DesktopSettings { port: 0, token: "desktop-test-token".into(), ..Default::default() };
        // Canonical Windows paths carry the same verbatim prefix Tauri uses
        // for installed resources, including installs on another drive.
        let script = fs::canonicalize(shipped_argus()).unwrap();
        let mut child = argus_command(&node, &script, None, &settings, home.path()).unwrap().spawn().unwrap();
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
        let stopped = terminate_process_tree(meta.runtime.pid).await;
        let exit = timeout(Duration::from_secs(10), exited).await;
        assert!(stopped.is_ok(), "stop failed: {stopped:?}; child: {exit:?}; alive: {}", crate::process::is_process_alive(meta.runtime.pid));
        if cfg!(unix) {
            assert!(exit.unwrap().unwrap().success(), "SIGTERM is a clean stop");
            assert!(!home.path().join("owner.json").exists(), "and gives the state root back");
        }
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn npm_cli_preflight_keeps_unicode_spaces_and_shell_metacharacters_literal() {
        let node = find_node(None).unwrap();
        let root = tempfile::tempdir().unwrap();
        let directory = root.path().join("中文 & spaces %PATH%");
        fs::create_dir(&directory).unwrap();
        let shim = directory.join("pi.cmd");
        fs::write(&shim, include_str!("../test-fixtures/npm-cli.cmd")).unwrap();
        fs::create_dir(directory.join("node_modules")).unwrap();
        fs::write(directory.join("node_modules/cli.js"), "if (process.argv[2] !== '--version') process.exit(8); console.log('fixture 1.0');").unwrap();
        let argv = runner_argv(&node, &shim).unwrap();
        let output = Command::new(&argv[0]).args(&argv[1..]).arg("--version")
            .env("Path", runtime_path(&node, Some(&shim)).unwrap()).output().await.unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        assert_eq!(String::from_utf8_lossy(&output.stdout).trim(), "fixture 1.0");
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
