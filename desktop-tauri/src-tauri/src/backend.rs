use crate::{
    identity::{
        authenticated_bundled_backend_matches, backend_launch_claim_matches,
        backend_ownership_matches, normalized_windows_path, prior_backend_ownership_matches,
        same_path, ExpectedBackendIdentity, ExpectedBackendLaunch, ExpectedPriorBackendOwnership,
    },
    logger::DesktopLogger,
    models::{
        BackendOwnership, BackendState, BackendStatus, DesktopSettings, ProbeFailureKind,
        ProbeIdentity,
    },
    process::{is_process_alive, terminate_windows_process_tree},
    redaction::redact_sensitive_text,
    release::ReleaseContext,
    resilience::{AutomaticRecoveryDecision, BackendResiliencePolicy, HealthDecision},
    runner::{detect_runners, resolve_runner_binary, runner_runtime_path_entries},
    settings::{runner_bin, SettingsStore},
};
use base64::Engine as _;
use chrono::Utc;
use rand::RngCore;
use reqwest::{header::AUTHORIZATION, Client};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{
    collections::VecDeque,
    env,
    ffi::OsString,
    fs,
    future::Future,
    path::{Path, PathBuf},
    pin::Pin,
    process::Stdio,
    sync::{Arc, Mutex},
    time::Duration,
};
use tauri::{AppHandle, Emitter};
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    net::TcpStream,
    process::Command,
    time::{sleep, timeout, Instant},
};

const INITIAL_PROBE_TIMEOUT: Duration = Duration::from_secs(2);
const HEALTH_PROBE_TIMEOUT: Duration = Duration::from_secs(3);
const HEALTH_INTERVAL: Duration = Duration::from_secs(5);
const STABLE_RESET_DELAY: Duration = Duration::from_secs(60);
const STARTUP_TIMEOUT: Duration = Duration::from_secs(30);
const RUNNER_PREFLIGHT_TIMEOUT: Duration = Duration::from_secs(8);
// A console-subsystem frozen backend must not flash a terminal window while
// the native desktop host starts it.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Clone)]
pub struct BackendSupervisor {
    inner: Arc<BackendSupervisorInner>,
}

struct BackendSupervisorInner {
    app: AppHandle,
    settings: Arc<SettingsStore>,
    release: ReleaseContext,
    logger: DesktopLogger,
    client: Client,
    runtime: Mutex<Runtime>,
}

struct Runtime {
    status: BackendStatus,
    root_pid: Option<u32>,
    runtime_pid: Option<u32>,
    stopping: bool,
    lifecycle_generation: u64,
    reached_ready: bool,
    recovery_scheduled: bool,
    resilience: BackendResiliencePolicy,
    log_tail: VecDeque<String>,
}

#[derive(Clone)]
struct BackendCommand {
    command: PathBuf,
    args: Vec<String>,
    cwd: PathBuf,
}

#[derive(Deserialize)]
struct MetaResponse {
    authentication: Option<MetaAuthentication>,
    runtime: Option<MetaRuntime>,
}

#[derive(Deserialize)]
struct MetaAuthentication {
    authenticated: Option<bool>,
}

#[derive(Deserialize)]
struct MetaRuntime {
    package_version: Option<String>,
    manifest_source_digest: Option<String>,
    executable: Option<String>,
    pid: Option<u32>,
    started_at: Option<String>,
    desktop_launch_nonce: Option<String>,
}

impl BackendSupervisor {
    pub fn new(
        app: AppHandle,
        settings: Arc<SettingsStore>,
        release: ReleaseContext,
        logger: DesktopLogger,
    ) -> anyhow::Result<Arc<Self>> {
        // Uvicorn's default keep-alive is five seconds, exactly matching the
        // supervisor heartbeat.  Keeping a pooled connection alive across that
        // boundary caused recurrent three-second health timeouts even though
        // the local API was responsive.  Keep short startup retries reusable,
        // but retire an idle connection well before the next heartbeat and
        // never send loopback identity probes through a host proxy.
        let client = Client::builder()
            .no_proxy()
            .pool_idle_timeout(Some(Duration::from_secs(1)))
            .build()?;
        Ok(Arc::new(Self {
            inner: Arc::new(BackendSupervisorInner {
                app,
                settings,
                release,
                logger,
                client,
                runtime: Mutex::new(Runtime {
                    status: BackendStatus::default(),
                    root_pid: None,
                    runtime_pid: None,
                    stopping: false,
                    lifecycle_generation: 0,
                    reached_ready: false,
                    recovery_scheduled: false,
                    resilience: BackendResiliencePolicy::default(),
                    log_tail: VecDeque::with_capacity(200),
                }),
            }),
        }))
    }

    pub fn current_status(&self) -> BackendStatus {
        let settings = self.inner.settings.snapshot();
        let runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
        let mut status = runtime.status.clone();
        status.pid = runtime.runtime_pid.or(runtime.root_pid);
        if status.state == BackendState::Ready {
            status.url = Some(SettingsStore::cockpit_url(&settings));
        }
        status
    }

    pub fn release(&self) -> &ReleaseContext {
        &self.inner.release
    }

    fn emit_status(&self) {
        let status = self.current_status();
        let _ = self.inner.app.emit("argus:status", status);
    }

    fn set_status(&self, state: BackendState, message: impl Into<String>, detail: Option<String>) {
        let message = message.into();
        {
            let mut runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
            runtime.status = BackendStatus {
                state: state.clone(),
                message: message.clone(),
                detail: detail.clone(),
                pid: None,
                url: None,
            };
        }
        let suffix = detail.as_deref().unwrap_or_default();
        self.inner
            .logger
            .info(format!("backend {state:?}: {message} {suffix}"));
        self.emit_status();
    }

    fn generation(&self) -> u64 {
        self.inner
            .runtime
            .lock()
            .expect("runtime mutex poisoned")
            .lifecycle_generation
    }

    fn is_current_generation(&self, generation: u64) -> bool {
        let runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
        runtime.lifecycle_generation == generation && !runtime.stopping
    }

    fn is_stopping(&self) -> bool {
        self.inner
            .runtime
            .lock()
            .expect("runtime mutex poisoned")
            .stopping
    }

    /// Box the public startup future so a crash-triggered recovery may schedule
    /// another startup without creating an infinitely-sized async future type.
    pub fn start(self: &Arc<Self>) -> Pin<Box<dyn Future<Output = ()> + Send>> {
        let supervisor = Arc::clone(self);
        Box::pin(async move {
            supervisor.start_inner().await;
        })
    }

    async fn start_inner(self: &Arc<Self>) {
        let generation = {
            let mut runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
            if matches!(
                runtime.status.state,
                BackendState::Starting | BackendState::Ready
            ) {
                return;
            }
            runtime.stopping = false;
            runtime.lifecycle_generation
        };
        let settings = self.inner.settings.snapshot();
        let command = match self.resolve_command(&settings) {
            Ok(command) => command,
            Err(error) => {
                self.set_status(
                    BackendState::Error,
                    "无法准备 Argus 本地运行环境",
                    Some(error.to_string()),
                );
                return;
            }
        };
        if !self.inner.release.development {
            if !command.command.is_file() {
                self.set_status(
                    BackendState::Error,
                    "内置 Argus 后端缺失",
                    Some(format!(
                        "未找到 {}；请重新安装完整桌面包。",
                        command.command.display()
                    )),
                );
                return;
            }
            if self.expected_manifest_digest().is_none() {
                self.set_status(
                    BackendState::Error,
                    "无法验证内置 Argus 后端",
                    Some("发布清单缺失或损坏；桌面端不会启动身份不明的后端。".to_owned()),
                );
                return;
            }
        }

        self.set_status(BackendState::Starting, "正在检查本地服务", None);
        let probe = self.probe_for_startup(&settings).await;
        if !self.is_current_generation(generation) {
            return;
        }
        if probe.compatible {
            if !self.ownership_matches(&probe, &settings, &command) {
                self.set_status(
                    BackendState::Error,
                    format!(
                        "端口 {} 已由未受当前桌面进程管理的 Argus 占用",
                        settings.port
                    ),
                    Some(
                        "请先正常退出另一份 Argus；桌面端不会接管或终止无法证明所有权的进程。"
                            .to_owned(),
                    ),
                );
                return;
            }
            {
                let mut runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
                runtime.runtime_pid = probe.pid;
            }
            self.mark_backend_ready(generation, "本地服务已就绪");
            return;
        }

        if probe.occupied {
            let may_replace = self.prior_ownership_matches(&probe, &settings)
                || self.legacy_bundled_backend_matches(&probe, &command);
            if !may_replace {
                self.set_status(
                    BackendState::Error,
                    format!("端口 {} 已被其他程序占用", settings.port),
                    probe.detail,
                );
                return;
            }
            self.set_status(
                BackendState::Starting,
                "正在升级受管理的 Argus 本地后端",
                probe.detail.clone(),
            );
            if !self
                .stop_prior_owned_backend(&probe, &settings, &command)
                .await
            {
                self.set_status(
                    BackendState::Error,
                    "无法安全替换上一版本的 Argus 本地后端",
                    Some("旧后端的身份已验证，但其监听进程未能在限定时间内退出；请从旧版 Argus 正常退出后重试。".to_owned()),
                );
                return;
            }
            if !self.is_current_generation(generation) {
                return;
            }
        }

        self.set_status(BackendState::Starting, "正在启动 Argus 本地后端", None);
        if let Err(error) = self.spawn_backend(command, settings, generation).await {
            self.handle_backend_failure("无法准备 Argus 本地运行环境", error.to_string())
                .await;
        }
    }

    pub async fn restart(self: &Arc<Self>) {
        self.stop().await;
        self.start().await;
    }

    pub async fn stop(self: &Arc<Self>) {
        {
            let mut runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
            runtime.lifecycle_generation = runtime.lifecycle_generation.wrapping_add(1);
            runtime.stopping = true;
            runtime.reached_ready = false;
            runtime.recovery_scheduled = false;
            runtime.resilience.reset();
        }
        self.terminate_owned_backend().await;
        self.set_status(BackendState::Stopped, "已停止", None);
    }

    async fn handle_backend_failure(self: &Arc<Self>, message: impl Into<String>, detail: String) {
        if self.is_stopping() {
            return;
        }
        let ready = self
            .inner
            .runtime
            .lock()
            .expect("runtime mutex poisoned")
            .reached_ready;
        if ready {
            self.schedule_automatic_recovery(detail).await;
        } else {
            self.set_status(BackendState::Error, message, Some(detail));
        }
    }

    async fn schedule_automatic_recovery(self: &Arc<Self>, detail: String) {
        let decision = {
            let mut runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
            if runtime.stopping || runtime.recovery_scheduled {
                return;
            }
            runtime.recovery_scheduled = true;
            runtime.resilience.begin_automatic_recovery()
        };
        let AutomaticRecoveryDecision::Allowed {
            attempt,
            max_attempts,
            delay_ms,
        } = decision
        else {
            let AutomaticRecoveryDecision::Denied {
                attempts,
                max_attempts,
            } = decision
            else {
                return;
            };
            self.inner
                .runtime
                .lock()
                .expect("runtime mutex poisoned")
                .recovery_scheduled = false;
            self.set_status(
                BackendState::Error,
                "Argus 本地服务无法自动恢复",
                Some(format!("{detail}\n已完成 {attempts}/{max_attempts} 次自动恢复；请检查诊断信息后手动重试。")),
            );
            return;
        };
        let generation = self.generation();
        self.set_status(
            BackendState::Starting,
            format!("本地服务短暂中断，正在自动恢复（{attempt}/{max_attempts}）"),
            Some(detail),
        );
        let supervisor = Arc::clone(self);
        tokio::spawn(async move {
            sleep(Duration::from_millis(delay_ms)).await;
            if !supervisor.is_current_generation(generation) {
                return;
            }
            supervisor.terminate_owned_backend().await;
            if !supervisor.is_current_generation(generation) {
                return;
            }
            {
                let mut runtime = supervisor
                    .inner
                    .runtime
                    .lock()
                    .expect("runtime mutex poisoned");
                runtime.status.state = BackendState::Idle;
                runtime.status.message = "自动恢复正在重新启动".to_owned();
                runtime.status.detail = None;
                runtime.recovery_scheduled = false;
            }
            supervisor.start().await;
        });
    }

    fn mark_backend_ready(self: &Arc<Self>, generation: u64, message: impl Into<String>) {
        if !self.is_current_generation(generation) {
            return;
        }
        let should_arm_stable_reset = {
            let mut runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
            runtime.reached_ready = true;
            runtime.recovery_scheduled = false;
            runtime.resilience.record_health_success();
            runtime.resilience.restart_attempt_count() > 0
        };
        self.set_status(BackendState::Ready, message, None);
        self.start_health_monitor(generation);
        if should_arm_stable_reset {
            let supervisor = Arc::clone(self);
            tokio::spawn(async move {
                sleep(STABLE_RESET_DELAY).await;
                if !supervisor.is_current_generation(generation) {
                    return;
                }
                let attempts = {
                    let mut runtime = supervisor
                        .inner
                        .runtime
                        .lock()
                        .expect("runtime mutex poisoned");
                    let attempts = runtime.resilience.restart_attempt_count();
                    runtime.resilience.mark_runtime_stable();
                    attempts
                };
                supervisor.inner.logger.info(format!("backend remained healthy; reset automatic recovery circuit after {attempts} attempt(s)"));
            });
        }
    }

    fn start_health_monitor(self: &Arc<Self>, generation: u64) {
        let supervisor = Arc::clone(self);
        tokio::spawn(async move {
            let mut delay = HEALTH_INTERVAL;
            loop {
                sleep(delay).await;
                if !supervisor.is_current_generation(generation)
                    || supervisor.current_status().state != BackendState::Ready
                {
                    return;
                }
                let settings = supervisor.inner.settings.snapshot();
                let command = match supervisor.resolve_command(&settings) {
                    Ok(command) => command,
                    Err(error) => {
                        supervisor.set_status(
                            BackendState::Error,
                            "无法验证本地后端",
                            Some(error.to_string()),
                        );
                        return;
                    }
                };
                let probe = supervisor.probe(&settings, HEALTH_PROBE_TIMEOUT).await;
                if !supervisor.is_current_generation(generation)
                    || supervisor.current_status().state != BackendState::Ready
                {
                    return;
                }
                if probe.compatible && supervisor.ownership_matches(&probe, &settings, &command) {
                    {
                        let mut runtime = supervisor
                            .inner
                            .runtime
                            .lock()
                            .expect("runtime mutex poisoned");
                        runtime.resilience.record_health_success();
                    }
                    delay = HEALTH_INTERVAL;
                    continue;
                }
                let pid = {
                    let runtime = supervisor
                        .inner
                        .runtime
                        .lock()
                        .expect("runtime mutex poisoned");
                    runtime.runtime_pid.or(runtime.root_pid)
                };
                let identity_conflict = probe.failure_kind == Some(ProbeFailureKind::Identity)
                    || (probe.compatible
                        && !supervisor.ownership_matches(&probe, &settings, &command));
                let decision = {
                    let mut runtime = supervisor
                        .inner
                        .runtime
                        .lock()
                        .expect("runtime mutex poisoned");
                    runtime
                        .resilience
                        .record_health_failure(identity_conflict, pid.is_some_and(is_process_alive))
                };
                let detail = probe.detail.unwrap_or_else(|| {
                    format!("127.0.0.1:{} 暂时未响应当前桌面构建", settings.port)
                });
                match decision {
                    HealthDecision::Retry { delay_ms, .. } => {
                        supervisor
                            .inner
                            .logger
                            .warn(format!("backend health probe transient failure: {detail}"));
                        delay = Duration::from_millis(delay_ms);
                    }
                    HealthDecision::Fail { .. } => {
                        supervisor.set_status(
                            BackendState::Error,
                            "Argus 本地后端身份验证失败",
                            Some(format!(
                                "{detail}\n桌面端不会接管、重启或终止无法证明所有权的进程。"
                            )),
                        );
                        return;
                    }
                    HealthDecision::Recover { .. } => {
                        supervisor.schedule_automatic_recovery(detail).await;
                        return;
                    }
                }
            }
        });
    }

    async fn probe_for_startup(&self, settings: &DesktopSettings) -> ProbeIdentity {
        let mut probe = self.probe(settings, INITIAL_PROBE_TIMEOUT).await;
        for delay in [Duration::from_millis(250), Duration::from_millis(750)] {
            if self.is_stopping()
                || !probe.occupied
                || probe.failure_kind == Some(ProbeFailureKind::Identity)
            {
                break;
            }
            sleep(delay).await;
            if self.is_stopping() {
                break;
            }
            probe = self.probe(settings, INITIAL_PROBE_TIMEOUT).await;
        }
        probe
    }

    async fn probe(&self, settings: &DesktopSettings, timeout_value: Duration) -> ProbeIdentity {
        let base = SettingsStore::api_base_url(settings);
        let request = self
            .inner
            .client
            .get(format!("{base}/api/meta"))
            .header(AUTHORIZATION, format!("Bearer {}", settings.token))
            .timeout(timeout_value);
        let response = match request.send().await {
            Ok(response) => response,
            Err(error) => {
                let occupied = self.port_occupied(settings).await;
                let timeout_failure = error.is_timeout();
                return ProbeIdentity {
                    compatible: false,
                    occupied,
                    authenticated: false,
                    detail: Some(if occupied {
                        format!(
                            "本地服务端口仍在监听，但健康检查在 {}ms 内未完成",
                            timeout_value.as_millis()
                        )
                    } else if timeout_failure {
                        format!("本地服务暂时不可达（{}ms 超时）", timeout_value.as_millis())
                    } else {
                        "本地服务暂时不可达".to_owned()
                    }),
                    pid: None,
                    executable: None,
                    manifest_source_digest: None,
                    started_at: None,
                    launch_nonce: None,
                    failure_kind: Some(if timeout_failure {
                        ProbeFailureKind::Timeout
                    } else {
                        ProbeFailureKind::Network
                    }),
                };
            }
        };
        if !response.status().is_success() {
            return ProbeIdentity {
                compatible: false,
                occupied: true,
                authenticated: false,
                detail: Some(format!("本地服务健康检查返回 HTTP {}", response.status())),
                pid: None,
                executable: None,
                manifest_source_digest: None,
                started_at: None,
                launch_nonce: None,
                failure_kind: Some(ProbeFailureKind::Http),
            };
        }
        let body = match response.json::<MetaResponse>().await {
            Ok(body) => body,
            Err(_) => {
                return ProbeIdentity {
                    compatible: false,
                    occupied: true,
                    authenticated: false,
                    detail: Some("端口上的服务返回了无法验证的 Argus 身份数据".to_owned()),
                    pid: None,
                    executable: None,
                    manifest_source_digest: None,
                    started_at: None,
                    launch_nonce: None,
                    failure_kind: Some(ProbeFailureKind::Identity),
                };
            }
        };
        let runtime = body.runtime;
        let authenticated = body
            .authentication
            .and_then(|auth| auth.authenticated)
            .unwrap_or(false);
        if !authenticated {
            return identity_probe("端口上的服务未通过当前 Argus 桌面端身份认证", false, None);
        }
        let Some(runtime) = runtime else {
            return identity_probe("端口上的服务缺少当前 Argus 桌面运行身份", true, None);
        };
        let package_version = runtime.package_version.clone();
        let probe = ProbeIdentity {
            compatible: false,
            occupied: true,
            authenticated: true,
            detail: None,
            pid: runtime.pid,
            executable: runtime.executable,
            manifest_source_digest: runtime.manifest_source_digest,
            started_at: runtime.started_at,
            launch_nonce: runtime.desktop_launch_nonce,
            failure_kind: None,
        };
        if probe.pid.filter(|value| *value > 0).is_none()
            || probe.executable.as_deref().map_or(true, str::is_empty)
            || probe.started_at.as_deref().map_or(true, str::is_empty)
        {
            return ProbeIdentity {
                detail: Some("端口上的服务缺少当前 Argus 桌面运行身份".to_owned()),
                failure_kind: Some(ProbeFailureKind::Identity),
                ..probe
            };
        }
        if !self.inner.release.development {
            let expected = self
                .resolve_command(settings)
                .ok()
                .map(|command| normalized_path(&command.command));
            if !probe.executable.as_deref().is_some_and(|value| {
                expected
                    .as_deref()
                    .is_some_and(|expected| same_path(value, expected))
            }) {
                return ProbeIdentity {
                    detail: Some(format!(
                        "端口由另一份 Argus 占用：{}",
                        probe.executable.clone().unwrap_or_default()
                    )),
                    failure_kind: Some(ProbeFailureKind::Identity),
                    ..probe
                };
            }
            if package_version.as_deref() != Some(self.inner.release.app_version.as_str()) {
                return ProbeIdentity {
                    detail: Some(format!(
                        "端口上的 Argus 版本为 {}，当前桌面版为 {}",
                        package_version.unwrap_or_default(),
                        self.inner.release.app_version
                    )),
                    failure_kind: Some(ProbeFailureKind::Identity),
                    ..probe
                };
            }
            if probe.manifest_source_digest.as_deref() != self.expected_manifest_digest().as_deref()
            {
                return ProbeIdentity {
                    detail: Some("端口上的 Argus 后端不是当前桌面构建".to_owned()),
                    failure_kind: Some(ProbeFailureKind::Identity),
                    ..probe
                };
            }
        }
        ProbeIdentity {
            compatible: true,
            occupied: false,
            ..probe
        }
    }

    async fn port_occupied(&self, settings: &DesktopSettings) -> bool {
        timeout(
            Duration::from_millis(500),
            TcpStream::connect(format!("{}:{}", settings.host, settings.port)),
        )
        .await
        .is_ok_and(|result| result.is_ok())
    }

    fn resolve_command(&self, settings: &DesktopSettings) -> anyhow::Result<BackendCommand> {
        if self.inner.release.development {
            let command = env::var_os("ARGUS_SKILL_BIN")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("python"));
            return Ok(BackendCommand {
                command,
                args: vec![
                    "-m".to_owned(),
                    "argus_skill".to_owned(),
                    "--web".to_owned(),
                    "--web-host".to_owned(),
                    settings.host.clone(),
                    "--web-port".to_owned(),
                    settings.port.to_string(),
                ],
                cwd: self.inner.release.repo_root.clone(),
            });
        }
        let command =
            self.inner.release.backend_executable().ok_or_else(|| {
                anyhow::anyhow!("bundled backend resource directory is unavailable")
            })?;
        Ok(BackendCommand {
            command,
            args: vec![
                "--web".to_owned(),
                "--web-host".to_owned(),
                settings.host.clone(),
                "--web-port".to_owned(),
                settings.port.to_string(),
            ],
            cwd: self.inner.settings.data_dir().to_path_buf(),
        })
    }

    fn expected_manifest_digest(&self) -> Option<String> {
        self.inner.release.manifest_digest()
    }

    fn token_sha256(settings: &DesktopSettings) -> String {
        format!("{:x}", Sha256::digest(settings.token.as_bytes()))
    }

    fn ownership_path(&self) -> PathBuf {
        self.inner
            .settings
            .data_dir()
            .join("runtime")
            .join("backend.json")
    }

    fn read_ownership(&self) -> Option<BackendOwnership> {
        fs::read_to_string(self.ownership_path())
            .ok()
            .and_then(|raw| serde_json::from_str(&raw).ok())
    }

    fn ownership_matches(
        &self,
        probe: &ProbeIdentity,
        settings: &DesktopSettings,
        command: &BackendCommand,
    ) -> bool {
        let Some(ownership) = self.read_ownership() else {
            return false;
        };
        let Some(digest) = self.expected_manifest_digest() else {
            return false;
        };
        let executable = if self.inner.release.development {
            probe.executable.clone().unwrap_or_default()
        } else {
            normalized_path(&command.command)
        };
        backend_ownership_matches(
            &ownership,
            probe,
            &ExpectedBackendIdentity {
                host: settings.host.clone(),
                port: settings.port,
                executable,
                manifest_source_digest: digest,
                token_sha256: Self::token_sha256(settings),
            },
        )
    }

    fn prior_ownership_matches(&self, probe: &ProbeIdentity, settings: &DesktopSettings) -> bool {
        let Some(ownership) = self.read_ownership() else {
            return false;
        };
        prior_backend_ownership_matches(
            &ownership,
            probe,
            &ExpectedPriorBackendOwnership {
                host: settings.host.clone(),
                port: settings.port,
                token_sha256: Self::token_sha256(settings),
            },
        )
    }

    fn legacy_bundled_backend_matches(
        &self,
        probe: &ProbeIdentity,
        command: &BackendCommand,
    ) -> bool {
        !self.inner.release.development
            && authenticated_bundled_backend_matches(probe, &normalized_path(&command.command))
    }

    async fn stop_prior_owned_backend(
        &self,
        probe: &ProbeIdentity,
        settings: &DesktopSettings,
        command: &BackendCommand,
    ) -> bool {
        let Some(pid) = probe.pid else { return false };
        if !(self.prior_ownership_matches(probe, settings)
            || self.legacy_bundled_backend_matches(probe, command))
        {
            return false;
        }
        self.inner.logger.info(format!(
            "replacing authenticated prior backend pid={pid} on {}:{}",
            settings.host, settings.port
        ));
        if is_process_alive(pid) && !terminate_windows_process_tree(pid).await {
            return false;
        }
        self.clear_ownership(Some(pid));
        let deadline = Instant::now() + Duration::from_secs(5);
        while self.port_occupied(settings).await {
            if Instant::now() >= deadline {
                return false;
            }
            sleep(Duration::from_millis(100)).await;
        }
        true
    }

    async fn spawn_backend(
        self: &Arc<Self>,
        command: BackendCommand,
        settings: DesktopSettings,
        generation: u64,
    ) -> anyhow::Result<()> {
        let manifest_source_digest = self.expected_manifest_digest().ok_or_else(|| {
            anyhow::anyhow!("Argus backend release manifest is missing or invalid")
        })?;
        self.ensure_special_prompts()?;
        let runtime_bin = self.ensure_runtime_command_shims(&command.command)?;
        let launch_nonce = random_nonce();
        let runner = runner_bin(&settings, &settings.runner_kind)
            .or_else(|| {
                env::var("ARGUS_SKILL_RUNNER_BIN")
                    .ok()
                    .filter(|value| !value.trim().is_empty())
            })
            .or_else(|| resolve_runner_binary(&settings.runner_kind));
        // The Rust host can locate an npm .cmd launcher even when the GUI
        // inherited a PATH without Node.  Supply its verified Node directory
        // to the frozen backend so every later AgentCliRunner subprocess gets
        // a runnable environment.  Python repeats this defensively for hosts
        // other than Tauri.
        let runner_runtime_paths = runner
            .as_deref()
            .map(runner_runtime_path_entries)
            .unwrap_or_default();
        let argus_home = env::var_os("ARGUS_SKILL_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                env::var_os("USERPROFILE")
                    .map(PathBuf::from)
                    .unwrap_or_else(|| PathBuf::from("."))
                    .join(".argus-skill")
            });
        let mut process = Command::new(&command.command);
        #[cfg(windows)]
        process.creation_flags(CREATE_NO_WINDOW);
        process
            .args(&command.args)
            .current_dir(&command.cwd)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(false)
            .env("ARGUS_BINARY_DISTRIBUTION", "1")
            .env("ARGUS_BINARY_MODE", "cli")
            .env("ARGUS_SKILL_BIN", &command.command)
            .env("ARGUS_SKILL_PYTHON", &command.command)
            .env("ARGUS_SKILL_WEB_TOKEN", &settings.token)
            .env("ARGUS_DESKTOP_LAUNCH_NONCE", &launch_nonce)
            .env("ARGUS_SKILL_HOME", argus_home)
            .env(
                "PYTHONUTF8",
                env::var("PYTHONUTF8").unwrap_or_else(|_| "1".to_owned()),
            )
            .env(
                "PYTHONIOENCODING",
                env::var("PYTHONIOENCODING").unwrap_or_else(|_| "utf-8".to_owned()),
            );
        if settings.runner_configured {
            process.env("ARGUS_SKILL_RUNNER_BACKEND", settings.runner_kind.as_str());
        }
        if let Some(runner) = runner.as_deref() {
            process.env("ARGUS_SKILL_RUNNER_BIN", runner);
            self.inner
                .logger
                .info(format!("resolved runner binary: {runner}"));
            if !runner_runtime_paths.is_empty() {
                self.inner.logger.info(format!(
                    "added Node runtime path for npm runner {}: {}",
                    runner,
                    runner_runtime_paths
                        .iter()
                        .map(|path| path.display().to_string())
                        .collect::<Vec<_>>()
                        .join(", ")
                ));
            }
        } else {
            self.inner.logger.warn(format!(
                "no {} binary found; configure ARGUS_SKILL_RUNNER_BIN or install it",
                settings.runner_kind.label()
            ));
        }
        let mut path_prefixes = runner_runtime_paths;
        if let Some(runtime_bin) = runtime_bin {
            path_prefixes.insert(0, runtime_bin.clone());
            process.env("ARGUS_SKILL_RUNTIME_BIN", runtime_bin);
        }
        let runner_path = if path_prefixes.is_empty() {
            None
        } else {
            let current_path = env::var_os("PATH").unwrap_or_default();
            Some(env::join_paths(
                path_prefixes
                    .into_iter()
                    .chain(env::split_paths(&current_path)),
            )?)
        };
        if let Some(path) = runner_path.as_ref() {
            process.env("PATH", path);
        }
        {
            let mut runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
            runtime.log_tail.clear();
        }
        let spawned_at_ms = Utc::now().timestamp_millis();
        let mut child = process.spawn()?;
        let root_pid = child
            .id()
            .ok_or_else(|| anyhow::anyhow!("backend process did not report a PID"))?;
        {
            let mut runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
            if runtime.lifecycle_generation != generation || runtime.stopping {
                return Err(anyhow::anyhow!("backend start was superseded"));
            }
            runtime.root_pid = Some(root_pid);
            runtime.runtime_pid = None;
        }
        if let Some(stdout) = child.stdout.take() {
            self.spawn_log_reader(stdout);
        }
        if let Some(stderr) = child.stderr.take() {
            self.spawn_log_reader(stderr);
        }
        if let Some(runner) = runner {
            let supervisor = Arc::clone(self);
            tokio::spawn(async move {
                supervisor.verify_runner_launch(runner, runner_path).await;
            });
        }
        let supervisor = Arc::clone(self);
        tokio::spawn(async move {
            let exit = child.wait().await;
            supervisor
                .handle_child_exit(root_pid, exit.map(|status| status.code()))
                .await;
        });
        let supervisor = Arc::clone(self);
        tokio::spawn(async move {
            supervisor
                .wait_until_ready(
                    root_pid,
                    settings,
                    generation,
                    ExpectedBackendLaunch {
                        launch_nonce,
                        manifest_source_digest,
                        spawned_at_ms,
                        now_ms: 0,
                    },
                )
                .await;
        });
        Ok(())
    }

    /// Validate the selected external runner in the exact PATH given to the
    /// frozen backend.  This intentionally checks only `--version`: startup
    /// stays non-blocking and never spends a model turn, but a stale GUI PATH
    /// can no longer remain invisible until every Manager message fails.
    async fn verify_runner_launch(&self, runner: String, path: Option<OsString>) {
        let mut command = Command::new(&runner);
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        command
            .arg("--version")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        if let Some(path) = path {
            command.env("PATH", path);
        }
        match timeout(RUNNER_PREFLIGHT_TIMEOUT, command.output()).await {
            Ok(Ok(output)) if output.status.success() => {
                self.inner
                    .logger
                    .info(format!("runner preflight passed: {runner}"));
            }
            Ok(Ok(output)) => {
                let stderr = redact_sensitive_text(&String::from_utf8_lossy(&output.stderr));
                let detail = stderr.trim();
                self.inner.logger.warn(format!(
                    "runner preflight failed for {runner} (exit {}): {}",
                    output
                        .status
                        .code()
                        .map_or_else(|| "none".to_owned(), |code| code.to_string()),
                    if detail.is_empty() {
                        "no stderr"
                    } else {
                        detail
                    }
                ));
            }
            Ok(Err(error)) => self.inner.logger.warn(format!(
                "runner preflight could not start {runner}: {error}"
            )),
            Err(_) => self.inner.logger.warn(format!(
                "runner preflight timed out after {}ms: {runner}",
                RUNNER_PREFLIGHT_TIMEOUT.as_millis()
            )),
        }
    }

    fn spawn_log_reader(
        self: &Arc<Self>,
        stream: impl tokio::io::AsyncRead + Unpin + Send + 'static,
    ) {
        let supervisor = Arc::clone(self);
        tokio::spawn(async move {
            let mut lines = BufReader::new(stream).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                supervisor.append_log(&line);
            }
        });
    }

    fn append_log(&self, line: &str) {
        // Uvicorn writes one access line for every health heartbeat and for the
        // cockpit's own readiness polling.  Persisting and flushing all of
        // those lines kept the desktop log hot indefinitely (tens of thousands
        // of writes in one session) while contributing no diagnostic signal.
        // Keep failures and every non-meta request; suppress only successful,
        // routine authenticated health access records.
        if is_routine_backend_access_log(line) {
            return;
        }
        let safe = redact_sensitive_text(line);
        {
            let mut runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
            runtime.log_tail.push_back(safe.clone());
            while runtime.log_tail.len() > 200 {
                runtime.log_tail.pop_front();
            }
        }
        self.inner.logger.verbose(format!("backend output: {safe}"));
    }

    async fn wait_until_ready(
        self: &Arc<Self>,
        root_pid: u32,
        settings: DesktopSettings,
        generation: u64,
        mut launch: ExpectedBackendLaunch,
    ) {
        let command = match self.resolve_command(&settings) {
            Ok(command) => command,
            Err(error) => {
                self.handle_backend_failure("本地后端就绪检查失败", error.to_string())
                    .await;
                return;
            }
        };
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        let mut last_detail = String::new();
        loop {
            if !self.is_current_generation(generation) {
                return;
            }
            let probe = self.probe(&settings, INITIAL_PROBE_TIMEOUT).await;
            if let Some(detail) = probe.detail.as_ref() {
                last_detail = detail.clone();
            }
            launch.now_ms = Utc::now().timestamp_millis();
            if backend_launch_claim_matches(&probe, &launch) {
                let runtime_pid = probe.pid.expect("launch match proves pid");
                let executable = probe
                    .executable
                    .clone()
                    .expect("launch match proves executable");
                let started_at = probe
                    .started_at
                    .clone()
                    .expect("launch match proves started timestamp");
                {
                    let mut runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
                    runtime.runtime_pid = Some(runtime_pid);
                }
                self.write_ownership(runtime_pid, root_pid, &executable, &started_at);
                if self.ownership_matches(&probe, &settings, &command) {
                    self.mark_backend_ready(generation, "Argus 桌面端已就绪");
                    return;
                }
                last_detail = "本地后端身份记录未能通过完整性校验".to_owned();
            } else if probe.compatible {
                last_detail = "响应端未能证明它属于本次桌面端启动".to_owned();
            }
            if Instant::now() >= deadline {
                self.cleanup_timed_out_backend(root_pid).await;
                self.handle_backend_failure(
                    "本地后端启动超时",
                    if last_detail.is_empty() {
                        self.log_tail()
                    } else {
                        last_detail
                    },
                )
                .await;
                return;
            }
            sleep(Duration::from_millis(500)).await;
        }
    }

    async fn handle_child_exit(
        self: &Arc<Self>,
        root_pid: u32,
        exit_code: Result<Option<i32>, std::io::Error>,
    ) {
        let (was_current, stopping, reached_ready, runtime_pid) = {
            let mut runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
            let was_current = runtime.root_pid == Some(root_pid);
            let stopping = runtime.stopping;
            let reached_ready = runtime.reached_ready;
            let runtime_pid = runtime.runtime_pid;
            if was_current {
                runtime.root_pid = None;
                runtime.runtime_pid = None;
            }
            (was_current, stopping, reached_ready, runtime_pid)
        };
        if !was_current {
            return;
        }
        if stopping {
            self.clear_dead_ownership();
            return;
        }
        if let Some(runtime_pid) = runtime_pid {
            if runtime_pid != root_pid && is_process_alive(runtime_pid) {
                if terminate_windows_process_tree(runtime_pid).await {
                    self.clear_ownership(Some(runtime_pid));
                }
            } else {
                self.clear_ownership(Some(runtime_pid));
            }
        } else {
            self.clear_dead_ownership();
        }
        let code = exit_code
            .ok()
            .flatten()
            .map_or_else(|| "none".to_owned(), |code| code.to_string());
        let detail = format!("exit code: {code}\n{}", self.log_tail());
        self.handle_backend_failure(
            if reached_ready {
                "Argus 本地后端意外退出"
            } else {
                "本地后端启动失败"
            },
            detail,
        )
        .await;
    }

    async fn cleanup_timed_out_backend(&self, root_pid: u32) {
        let runtime_pid = {
            let mut runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
            if runtime.root_pid == Some(root_pid) {
                runtime.root_pid = None;
            }
            let runtime_pid = runtime.runtime_pid;
            runtime.runtime_pid = None;
            runtime_pid
        };
        let mut terminated = true;
        if is_process_alive(root_pid) {
            terminated &= terminate_windows_process_tree(root_pid).await;
        }
        if let Some(runtime_pid) =
            runtime_pid.filter(|pid| *pid != root_pid && is_process_alive(*pid))
        {
            terminated &= terminate_windows_process_tree(runtime_pid).await;
        }
        if let Some(runtime_pid) = runtime_pid {
            if terminated && !is_process_alive(root_pid) && !is_process_alive(runtime_pid) {
                self.clear_ownership(Some(runtime_pid));
            }
        }
    }

    async fn terminate_owned_backend(&self) {
        let (root_pid, runtime_pid) = {
            let mut runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
            (runtime.root_pid.take(), runtime.runtime_pid.take())
        };
        let mut terminated = true;
        if let Some(root_pid) = root_pid.filter(|pid| is_process_alive(*pid)) {
            terminated &= terminate_windows_process_tree(root_pid).await;
        }
        if let Some(runtime_pid) =
            runtime_pid.filter(|pid| Some(*pid) != root_pid && is_process_alive(*pid))
        {
            terminated &= terminate_windows_process_tree(runtime_pid).await;
        }
        if let Some(runtime_pid) = runtime_pid {
            if terminated && !is_process_alive(runtime_pid) {
                self.clear_ownership(Some(runtime_pid));
            } else if is_process_alive(runtime_pid) {
                self.inner.logger.error(format!("backend process tree remained alive; retaining ownership record runtime_pid={runtime_pid}"));
            }
        } else {
            self.clear_dead_ownership();
        }
    }

    fn write_ownership(&self, pid: u32, root_pid: u32, executable: &str, started_at: &str) {
        if pid == 0 || root_pid == 0 {
            return;
        }
        let Some(manifest_source_digest) = self.expected_manifest_digest() else {
            return;
        };
        let settings = self.inner.settings.snapshot();
        let token_sha256 = Self::token_sha256(&settings);
        let ownership = BackendOwnership {
            schema: 3,
            pid,
            root_pid,
            host: settings.host,
            port: settings.port,
            executable: normalized_path(Path::new(executable)),
            manifest_source_digest,
            token_sha256,
            started_at: started_at.to_owned(),
        };
        let path = self.ownership_path();
        if let Some(parent) = path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        if let Ok(payload) = serde_json::to_vec_pretty(&ownership) {
            let _ = fs::write(path, payload);
        }
    }

    fn clear_ownership(&self, expected_pid: Option<u32>) {
        let path = self.ownership_path();
        if let Some(expected_pid) = expected_pid {
            let matches = fs::read_to_string(&path)
                .ok()
                .and_then(|raw| serde_json::from_str::<BackendOwnership>(&raw).ok())
                .is_some_and(|record| record.pid == expected_pid);
            if !matches {
                return;
            }
        }
        let _ = fs::remove_file(path);
    }

    fn clear_dead_ownership(&self) {
        let Some(ownership) = self.read_ownership() else {
            return;
        };
        if ownership.pid > 0
            && !is_process_alive(ownership.pid)
            && ownership.root_pid > 0
            && !is_process_alive(ownership.root_pid)
        {
            self.clear_ownership(Some(ownership.pid));
        }
    }

    fn ensure_special_prompts(&self) -> anyhow::Result<()> {
        let home = env::var_os("ARGUS_SKILL_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                env::var_os("USERPROFILE")
                    .map(PathBuf::from)
                    .unwrap_or_else(|| PathBuf::from("."))
                    .join(".argus-skill")
            });
        let file = home.join("special_prompts").join("10-house-rules.md");
        if !file.is_file() {
            if let Some(parent) = file.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(&file, "Operational house rules for this machine.\n")?;
            self.inner.logger.info(format!(
                "created default operator prompts at {}",
                file.display()
            ));
        }
        Ok(())
    }

    fn ensure_runtime_command_shims(&self, command: &Path) -> anyhow::Result<Option<PathBuf>> {
        if self.inner.release.development {
            return Ok(None);
        }
        let runtime_bin = self.inner.settings.data_dir().join("runtime").join("bin");
        fs::create_dir_all(&runtime_bin)?;
        let escaped = command.to_string_lossy().replace('%', "%%");
        let cmd_body = format!("@echo off\r\n\"{escaped}\" %*\r\n");
        for name in ["python.cmd", "python3.cmd"] {
            fs::write(runtime_bin.join(name), &cmd_body)?;
        }
        let shell = command
            .to_string_lossy()
            .replace('\\', "/")
            .replace('\'', "'\"'\"'");
        let shell_body = format!("#!/bin/sh\nexec '{shell}' \"$@\"\n");
        for name in ["python", "python3"] {
            fs::write(runtime_bin.join(name), &shell_body)?;
        }
        Ok(Some(runtime_bin))
    }

    fn log_tail(&self) -> String {
        let runtime = self.inner.runtime.lock().expect("runtime mutex poisoned");
        runtime
            .log_tail
            .iter()
            .rev()
            .take(8)
            .cloned()
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect::<Vec<_>>()
            .join("\n")
    }

    pub fn detected_runners(&self) -> std::collections::BTreeMap<String, String> {
        detect_runners()
    }
}

fn is_routine_backend_access_log(line: &str) -> bool {
    line.contains("\"GET /api/meta HTTP/1.1\" 200")
}

fn identity_probe(
    message: &str,
    authenticated: bool,
    probe: Option<ProbeIdentity>,
) -> ProbeIdentity {
    let base = probe.unwrap_or(ProbeIdentity {
        compatible: false,
        occupied: true,
        authenticated,
        detail: None,
        pid: None,
        executable: None,
        manifest_source_digest: None,
        started_at: None,
        launch_nonce: None,
        failure_kind: None,
    });
    ProbeIdentity {
        detail: Some(message.to_owned()),
        failure_kind: Some(ProbeFailureKind::Identity),
        ..base
    }
}

fn normalized_path(path: &Path) -> String {
    let resolved = fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    normalized_windows_path(&resolved.to_string_lossy())
}

fn random_nonce() -> String {
    let mut bytes = [0_u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
}

#[cfg(test)]
mod tests {
    #[test]
    fn successful_meta_access_logs_are_not_persisted_as_diagnostics() {
        assert!(super::is_routine_backend_access_log(
            "INFO: 127.0.0.1 - \"GET /api/meta HTTP/1.1\" 200 OK"
        ));
        assert!(!super::is_routine_backend_access_log(
            "INFO: 127.0.0.1 - \"GET /api/meta HTTP/1.1\" 401 Unauthorized"
        ));
        assert!(!super::is_routine_backend_access_log(
            "INFO: 127.0.0.1 - \"GET /api/projects/s-1/events HTTP/1.1\" 200 OK"
        ));
    }
}
