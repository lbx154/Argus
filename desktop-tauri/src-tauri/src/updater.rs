use crate::{
    logger::DesktopLogger,
    models::{UpdateState, UpdateStatus},
    release::ReleaseContext,
    update_policy::{
        automatic_check_due, is_strictly_newer, should_present_cached_update, UpdateCache,
    },
};
use chrono::Utc;
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tauri::{AppHandle, Emitter};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_updater::UpdaterExt;
use tokio::sync::Mutex as AsyncMutex;

const AUTO_CHECK_INTERVAL_SECONDS: i64 = 6 * 60 * 60;
const AUTO_RETRY_BASE_SECONDS: i64 = 15 * 60;
const AUTO_RETRY_MAX_SECONDS: i64 = 2 * 60 * 60;
const AUTO_START_DELAY: Duration = Duration::from_secs(30);
const UPDATE_CHECK_TIMEOUT: Duration = Duration::from_secs(15);
const UPDATE_DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(20 * 60);
const PROGRESS_EMIT_INTERVAL: Duration = Duration::from_millis(200);
const NOTE_LIMIT: usize = 1_500;

#[derive(Clone)]
pub struct UpdateManager {
    inner: Arc<UpdateManagerInner>,
}

struct UpdateManagerInner {
    app: AppHandle,
    data_dir: PathBuf,
    logger: DesktopLogger,
    enabled: bool,
    status: Mutex<UpdateStatus>,
    operation: AsyncMutex<()>,
}

fn bounded_notes(notes: Option<String>) -> Option<String> {
    notes.and_then(|value| {
        let trimmed = value.trim();
        (!trimmed.is_empty()).then(|| trimmed.chars().take(NOTE_LIMIT).collect())
    })
}

fn make_status(
    state: UpdateState,
    current_version: String,
    available_version: Option<String>,
    notes: Option<String>,
    progress: Option<u8>,
    detail: Option<String>,
    user_initiated: bool,
) -> UpdateStatus {
    UpdateStatus {
        state,
        current_version,
        user_initiated,
        available_version,
        notes,
        progress,
        detail,
    }
}

fn read_cache(data_dir: &Path) -> UpdateCache {
    fs::read_to_string(data_dir.join("update-check.json"))
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_default()
}

fn cached_available_status(current: &str, cache: &UpdateCache) -> Option<UpdateStatus> {
    let candidate = cache.available_version.as_deref()?.trim();
    if !should_present_cached_update(candidate, current, cache.dismissed_version.as_deref()) {
        return None;
    }
    Some(make_status(
        UpdateState::Available,
        current.to_owned(),
        Some(candidate.to_owned()),
        bounded_notes(cache.available_notes.clone()),
        None,
        None,
        false,
    ))
}

impl UpdateManager {
    pub fn new(
        app: AppHandle,
        data_dir: PathBuf,
        release: &ReleaseContext,
        logger: DesktopLogger,
    ) -> Arc<Self> {
        let cached = read_cache(&data_dir);
        let status = cached_available_status(&release.app_version, &cached).unwrap_or_default();
        let update_checks_disabled =
            std::env::var("ARGUS_DESKTOP_DISABLE_UPDATE_CHECK").as_deref() == Ok("1");
        Arc::new(Self {
            inner: Arc::new(UpdateManagerInner {
                app,
                data_dir,
                logger,
                enabled: !release.development && !update_checks_disabled,
                status: Mutex::new(status),
                operation: AsyncMutex::new(()),
            }),
        })
    }

    pub fn status(&self) -> UpdateStatus {
        self.inner
            .status
            .lock()
            .expect("update status mutex poisoned")
            .clone()
    }

    fn replace_status(&self, status: UpdateStatus, emit: bool) {
        *self
            .inner
            .status
            .lock()
            .expect("update status mutex poisoned") = status.clone();
        if emit {
            let _ = self.inner.app.emit("argus:update-status", status);
        }
    }

    fn set_status(&self, status: UpdateStatus) {
        self.replace_status(status, true);
    }

    fn set_status_quietly(&self, status: UpdateStatus) {
        self.replace_status(status, false);
    }

    fn cache_path(&self) -> PathBuf {
        self.inner.data_dir.join("update-check.json")
    }

    fn cache(&self) -> UpdateCache {
        read_cache(&self.inner.data_dir)
    }

    fn save_cache(&self, cache: &UpdateCache) {
        if let Ok(payload) = serde_json::to_vec_pretty(cache) {
            let _ = fs::write(self.cache_path(), payload);
        }
    }

    pub fn spawn_automatic_check(self: &Arc<Self>) {
        if !self.inner.enabled {
            return;
        }
        let manager = Arc::clone(self);
        tauri::async_runtime::spawn(async move {
            // Let the authenticated cockpit mount before the background network
            // check starts. It remains entirely silent unless a newer package is
            // actually found.
            tokio::time::sleep(AUTO_START_DELAY).await;
            let _ = manager.check(false).await;
        });
    }

    pub async fn check(self: &Arc<Self>, manual: bool) -> UpdateStatus {
        let _operation = self.inner.operation.lock().await;
        self.check_inner(manual).await
    }

    async fn check_inner(&self, manual: bool) -> UpdateStatus {
        let prior_status = self.status();
        let current = prior_status.current_version.clone();
        let had_presented_update = prior_status.state == UpdateState::Available;
        if !self.inner.enabled {
            let status = make_status(
                UpdateState::Idle,
                current,
                None,
                None,
                None,
                Some("开发构建不检查发布更新。".to_owned()),
                manual,
            );
            if manual {
                self.set_status(status.clone());
            } else {
                self.set_status_quietly(status.clone());
            }
            return status;
        }

        let mut cache = self.cache();
        let now = Utc::now().timestamp();
        if !manual
            && !automatic_check_due(
                now,
                cache.last_checked_at,
                cache.last_attempt_at,
                cache.consecutive_failures,
                AUTO_CHECK_INTERVAL_SECONDS,
                AUTO_RETRY_BASE_SECONDS,
                AUTO_RETRY_MAX_SECONDS,
            )
        {
            return self.status();
        }
        // Persist the attempt before network I/O. A crash or forced shutdown
        // cannot create a tight update-check loop on the next launch.
        cache.last_attempt_at = Some(now);
        self.save_cache(&cache);

        let checking = make_status(
            UpdateState::Checking,
            current.clone(),
            None,
            None,
            None,
            None,
            manual,
        );
        if manual {
            self.set_status(checking);
        }
        // A silent check does not replace an already-presented cached update
        // with an internal Checking state. The card remains actionable while
        // transport verification runs in the background.

        let result = self
            .inner
            .app
            .updater_builder()
            .timeout(UPDATE_CHECK_TIMEOUT)
            .build();
        let status = match result {
            Ok(updater) => match updater.check().await {
                Ok(Some(update)) => {
                    let candidate = update.version.to_string();
                    let notes = bounded_notes(update.body);
                    cache.last_checked_at = Some(now);
                    cache.consecutive_failures = 0;
                    if !is_strictly_newer(&candidate, &current) {
                        cache.available_version = None;
                        cache.available_notes = None;
                        self.save_cache(&cache);
                        self.inner.logger.warn(format!(
                            "ignored non-newer signed update candidate: {} -> {}",
                            current, candidate
                        ));
                        make_status(
                            UpdateState::UpToDate,
                            current,
                            None,
                            None,
                            None,
                            None,
                            manual,
                        )
                    } else {
                        cache.available_version = Some(candidate.clone());
                        cache.available_notes = notes.clone();
                        let dismissed =
                            cache.dismissed_version.as_deref() == Some(candidate.as_str());
                        let announce = !manual
                            && !dismissed
                            && cache.announced_version.as_deref() != Some(candidate.as_str());
                        if announce {
                            cache.announced_version = Some(candidate.clone());
                        }
                        self.save_cache(&cache);
                        if dismissed && !manual {
                            make_status(UpdateState::Idle, current, None, None, None, None, false)
                        } else {
                            if announce {
                                let _ = self
                                    .inner
                                    .app
                                    .notification()
                                    .builder()
                                    .title(format!("Argus {candidate} 可更新"))
                                    .body("发现新版本；安装前会验证更新包签名。")
                                    .show();
                            }
                            make_status(
                                UpdateState::Available,
                                current,
                                Some(candidate),
                                notes,
                                None,
                                None,
                                manual,
                            )
                        }
                    }
                }
                Ok(None) => {
                    cache.last_checked_at = Some(now);
                    cache.consecutive_failures = 0;
                    cache.available_version = None;
                    cache.available_notes = None;
                    self.save_cache(&cache);
                    make_status(
                        UpdateState::UpToDate,
                        current,
                        None,
                        None,
                        None,
                        None,
                        manual,
                    )
                }
                Err(error) => {
                    cache.consecutive_failures = cache.consecutive_failures.saturating_add(1);
                    self.save_cache(&cache);
                    let detail = format!("无法从签名更新源检查更新：{error}");
                    self.inner.logger.warn(&detail);
                    if !manual {
                        cached_available_status(&current, &cache).unwrap_or_else(|| {
                            make_status(
                                UpdateState::Error,
                                current,
                                None,
                                None,
                                None,
                                Some(detail),
                                false,
                            )
                        })
                    } else {
                        make_status(
                            UpdateState::Error,
                            current,
                            None,
                            None,
                            None,
                            Some(detail),
                            true,
                        )
                    }
                }
            },
            Err(error) => {
                cache.consecutive_failures = cache.consecutive_failures.saturating_add(1);
                self.save_cache(&cache);
                let detail = format!("更新器不可用：{error}");
                self.inner.logger.warn(&detail);
                if !manual {
                    cached_available_status(&current, &cache).unwrap_or_else(|| {
                        make_status(
                            UpdateState::Error,
                            current,
                            None,
                            None,
                            None,
                            Some(detail),
                            false,
                        )
                    })
                } else {
                    make_status(
                        UpdateState::Error,
                        current,
                        None,
                        None,
                        None,
                        Some(detail),
                        true,
                    )
                }
            }
        };

        if status.state == UpdateState::Available {
            self.inner.logger.info(format!(
                "signed desktop update available: {} -> {}",
                status.current_version,
                status.available_version.as_deref().unwrap_or("unknown")
            ));
        }
        // Background checks deliberately do not emit "checking", "current",
        // or transient-error UI. They emit only a presentable new update; a
        // manual menu action always receives visible feedback.
        if manual || status.state == UpdateState::Available || had_presented_update {
            // Emitting a non-visible background result is necessary only when
            // it retracts a previously cached update card. Ordinary current/
            // error checks stay silent.
            self.set_status(status.clone());
        } else {
            self.set_status_quietly(status.clone());
        }
        status
    }

    pub async fn install(self: &Arc<Self>) -> Result<(), String> {
        let _operation = self.inner.operation.lock().await;
        if !self.inner.enabled {
            return Err("开发构建不安装发布更新。".to_owned());
        }
        let current = self.status().current_version;
        self.set_status(make_status(
            UpdateState::Checking,
            current.clone(),
            None,
            None,
            None,
            None,
            true,
        ));
        let updater = self
            .inner
            .app
            .updater_builder()
            .timeout(UPDATE_CHECK_TIMEOUT)
            .build()
            .map_err(|error| {
                let detail = format!("更新器不可用：{error}");
                self.inner.logger.warn(&detail);
                self.set_status(make_status(
                    UpdateState::Error,
                    current.clone(),
                    None,
                    None,
                    None,
                    Some(detail.clone()),
                    true,
                ));
                detail
            })?;
        let update = updater.check().await.map_err(|error| {
            let detail = format!("无法从签名更新源检查更新：{error}");
            self.inner.logger.warn(&detail);
            self.set_status(make_status(
                UpdateState::Error,
                current.clone(),
                None,
                None,
                None,
                Some(detail.clone()),
                true,
            ));
            detail
        })?;
        let Some(mut update) = update else {
            let mut cache = self.cache();
            cache.available_version = None;
            cache.available_notes = None;
            self.save_cache(&cache);
            self.set_status(make_status(
                UpdateState::UpToDate,
                current,
                None,
                None,
                None,
                None,
                true,
            ));
            return Ok(());
        };
        // The manifest check is bounded tightly, while an explicitly approved
        // installer download gets enough time for slower networks.
        update.timeout = Some(UPDATE_DOWNLOAD_TIMEOUT);
        let candidate = update.version.to_string();
        if !is_strictly_newer(&candidate, &current) {
            self.set_status(make_status(
                UpdateState::UpToDate,
                current,
                None,
                None,
                None,
                None,
                true,
            ));
            return Ok(());
        }
        let notes = bounded_notes(update.body.clone());
        self.set_status(make_status(
            UpdateState::Downloading,
            current.clone(),
            Some(candidate.clone()),
            notes,
            Some(0),
            None,
            true,
        ));
        let manager = Arc::clone(self);
        let mut downloaded = 0_u64;
        let mut last_progress = Some(0_u8);
        let mut last_progress_emit = Instant::now();
        let result = update
            .download_and_install(
                move |chunk_length, content_length| {
                    downloaded = downloaded.saturating_add(chunk_length as u64);
                    let progress = content_length
                        .filter(|total| *total > 0)
                        .map(|total| ((downloaded.saturating_mul(100) / total).min(100)) as u8);
                    let changed = progress != last_progress;
                    let due = last_progress_emit.elapsed() >= PROGRESS_EMIT_INTERVAL;
                    if progress == Some(100) || (changed && due) {
                        let mut status = manager.status();
                        status.state = UpdateState::Downloading;
                        status.user_initiated = true;
                        status.progress = progress;
                        manager.set_status(status);
                        last_progress = progress;
                        last_progress_emit = Instant::now();
                    }
                },
                {
                    let manager = Arc::clone(self);
                    move || {
                        let mut status = manager.status();
                        status.state = UpdateState::Installing;
                        status.user_initiated = true;
                        status.progress = Some(100);
                        manager.set_status(status);
                    }
                },
            )
            .await;
        result.map_err(|error| {
            let detail = format!("更新下载或签名验证失败：{error}");
            self.inner.logger.warn(&detail);
            self.set_status(make_status(
                UpdateState::Error,
                current,
                Some(candidate),
                None,
                None,
                Some(detail.clone()),
                true,
            ));
            detail
        })
    }

    pub fn dismiss(&self) {
        let status = self.status();
        let mut cache = self.cache();
        if let Some(version) = status.available_version.as_ref() {
            cache.dismissed_version = Some(version.clone());
        }
        self.save_cache(&cache);
        self.set_status(make_status(
            UpdateState::Idle,
            status.current_version,
            None,
            None,
            None,
            None,
            false,
        ));
    }
}
