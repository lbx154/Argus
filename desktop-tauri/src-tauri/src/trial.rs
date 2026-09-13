//! Private desktop trial transport. Never place a Key in argv or browser storage.
use crate::{release::ReleaseContext, runner::argus_home_dir};
use serde::{Deserialize, Serialize};
use std::{env, fs, io::Write, path::{Path, PathBuf}, process::Stdio, time::Duration};
use tauri::{AppHandle, Emitter};
use tokio::{io::{AsyncBufReadExt, AsyncWriteExt, BufReader}, process::Command, time::timeout};

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TrialSetupInput { pub api_key: String }

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TrialBalance {
    #[serde(default, alias = "tokens_remaining")]
    pub tokens_remaining: Option<u64>,
    #[serde(default, alias = "token_limit")]
    pub token_limit: Option<u64>,
    #[serde(default, alias = "tokens_used")]
    pub tokens_used: Option<u64>,
    #[serde(default, alias = "checked_at")]
    pub checked_at: Option<u64>,
    #[serde(default)]
    pub stale: bool,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub paused: bool,
    #[serde(default)]
    pub attention: Option<String>,
}

#[derive(Clone, Deserialize, Serialize)]
struct DownloadProgress {
    downloaded_bytes: u64,
    total_bytes: Option<u64>,
}

#[derive(Deserialize)]
#[serde(tag = "event", rename_all = "lowercase")]
enum Event {
    Progress { message: String },
    Download {
        #[serde(flatten)]
        progress: DownloadProgress,
    },
    Complete { runner_bin: String, balance: TrialBalance },
    Balance { #[serde(flatten)] balance: TrialBalance },
    Error { message: String },
}

pub struct PreparedTrial { pub runner_bin: String, pub balance: TrialBalance }

pub fn valid_key(key: &str) -> bool {
    key.strip_prefix("argus_trial_").is_some_and(|value| value.len() == 64
        && value.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()))
}

async fn helper(app: &AppHandle, release: &ReleaseContext, key: Option<&str>, resume: bool) -> Result<Event, String> {
    release.validate_payload()?;
    let executable = if release.development {
        env::var_os("ARGUS_SKILL_BIN").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("python"))
    } else { release.backend_executable().ok_or("找不到桌面运行环境，请重新解压完整预览包。")? };
    let mut command = Command::new(executable);
    fs::create_dir_all(argus_home_dir()).map_err(|_| "无法创建本地配置目录。")?;
    #[cfg(windows)]
    command.creation_flags(0x0800_0000);
    let mut child = command.args(["-m", "argus_skill.trial.desktop"])
        .current_dir(if release.development { release.repo_root.clone() } else { argus_home_dir() })
        .env("ARGUS_SKILL_HOME", argus_home_dir())
        .env("PYTHONUTF8", "1").env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
        .kill_on_drop(true).spawn().map_err(|_| "无法启动试用服务，请重新解压预览包。")?;
    let request = match key {
        Some(key) => serde_json::json!({"action":"prepare", "api_key":key}),
        None => serde_json::json!({"action":if resume { "resume" } else { "status" }}),
    };
    let mut input = child.stdin.take().ok_or("无法创建安全输入通道。")?;
    input.write_all(format!("{request}\n").as_bytes()).await.map_err(|_| "无法传入试用请求。")?;
    drop(input);
    let output = child.stdout.take().ok_or("无法读取试用状态。")?;
    let operation = async {
        let mut lines = BufReader::new(output).lines();
        let mut completed = None;
        while let Some(line) = lines.next_line().await.map_err(|_| "读取试用状态失败。")? {
            if line.len() > 16_384 { return Err("试用服务返回了过大的状态信息。".to_owned()); }
            let event: Event = serde_json::from_str(&line).map_err(|_| "试用服务返回了无效结果。")?;
            let hide = |message: String| match key { Some(k) => message.replace(k, "[隐藏]"), None => message };
            match event {
                Event::Progress { message } => { let _ = app.emit("argus:trial-progress", hide(message)); }
                Event::Download { progress } => { let _ = app.emit("argus:trial-download", progress); }
                Event::Error { message } => return Err(hide(message)),
                event => completed = Some(event),
            }
        }
        let status = child.wait().await.map_err(|_| "无法确认试用状态。")?;
        if !status.success() { return Err("试用操作未完成，请重试。".to_owned()); }
        completed.ok_or_else(|| "试用服务未返回结果。".to_owned())
    };
    timeout(Duration::from_secs(if key.is_some() { 600 } else { 45 }), operation).await
        .map_err(|_| "试用准备或余额查询超时，请检查网络后重试。".to_owned())?
}

pub async fn configure(app: &AppHandle, release: &ReleaseContext, key: &str) -> Result<PreparedTrial, String> {
    if !valid_key(key) { return Err("请输入完整的内部测试 Key。".to_owned()); }
    match helper(app, release, Some(key), false).await? {
        Event::Complete { runner_bin, balance } if Path::new(&runner_bin).is_file() => Ok(PreparedTrial {runner_bin, balance}),
        _ => Err("未找到验证通过的 Copilot。".to_owned()),
    }
}

pub async fn status(app: &AppHandle, release: &ReleaseContext, resume: bool) -> Result<TrialBalance, String> {
    match helper(app, release, None, resume).await? {
        Event::Balance { balance } => Ok(balance),
        _ => Err("余额查询没有返回有效结果。".to_owned()),
    }
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path.parent().ok_or("无效的配置路径。")?;
    fs::create_dir_all(parent).map_err(|_| "无法创建本地配置目录。")?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent).map_err(|_| "无法保存试用配置。")?;
    temporary.write_all(bytes).map_err(|_| "无法写入试用配置。")?;
    temporary.as_file().sync_all().map_err(|_| "无法同步试用配置。")?;
    temporary.persist(path).map_err(|_| "无法应用试用配置。")?;
    Ok(())
}

/// Profile bytes are private to this native transaction. A failed settings
/// save/restart restores the previous Key before the previous backend restarts.
pub struct ProfileChange { path: PathBuf, previous: Option<Vec<u8>>, committed: bool }
impl ProfileChange {
    pub fn begin(key: &str) -> Result<Self, String> {
        if !valid_key(key) { return Err("无效的内部测试 Key。".to_owned()); }
        let path = argus_home_dir().join("copilot-trial.json");
        Self::at(path, key)
    }
    fn at(path: PathBuf, key: &str) -> Result<Self, String> {
        if fs::symlink_metadata(&path).is_ok_and(|m| m.file_type().is_symlink()) {
            return Err("试用配置不能是符号链接。".to_owned());
        }
        let previous = match fs::read(&path) {
            Ok(bytes) => Some(bytes),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
            Err(_) => return Err("无法读取原试用配置，未作更改。".to_owned()),
        };
        let data = serde_json::json!({"base_url":"https://argusbot.cn/v1", "api_key":key});
        atomic_write(&path, data.to_string().as_bytes())?;
        Ok(Self {path, previous, committed:false})
    }
    pub fn commit(mut self) {
        self.committed = true;
        if let Some(home) = self.path.parent() {
            let _ = fs::remove_file(home.join("trial-attention.json"));
        }
    }
    pub fn rollback(&mut self) -> Result<(), String> {
        if self.committed { return Ok(()); }
        match &self.previous {
            Some(bytes) => atomic_write(&self.path, bytes)?,
            None => match fs::remove_file(&self.path) {
                Ok(()) => (),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
                Err(_) => return Err("恢复原试用配置失败，请勿继续任务。".to_owned()),
            },
        }
        self.committed = true;
        Ok(())
    }
}
impl Drop for ProfileChange { fn drop(&mut self) { let _ = self.rollback(); } }

pub fn cache_balance(balance: &TrialBalance) {
    // Cached public quota is not an authority and must never contain a Key.
    let data = serde_json::json!({"tokens_remaining":balance.tokens_remaining,
        "token_limit":balance.token_limit,"tokens_used":balance.tokens_used,
        "checked_at":balance.checked_at,"stale":balance.stale});
    let _ = atomic_write(&argus_home_dir().join("trial-status.json"), data.to_string().as_bytes());
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn strict_key_validation() {
        assert!(valid_key(&format!("argus_trial_{}", "a".repeat(64))));
        assert!(!valid_key("argus_trial_short"));
        assert!(!valid_key(&format!("argus_trial_{}", "A".repeat(64))));
    }
    #[test]
    fn failed_activation_restores_previous_bytes() {
        let dir=tempfile::tempdir().unwrap();let file=dir.path().join("profile.json");
        fs::write(&file,b"old profile").unwrap();
        { let _guard=ProfileChange::at(file.clone(),"test-only").unwrap(); }
        assert_eq!(fs::read(&file).unwrap(),b"old profile");
    }
    #[test]
    fn first_failed_activation_leaves_no_profile() {
        let dir=tempfile::tempdir().unwrap();let file=dir.path().join("profile.json");
        { let _guard=ProfileChange::at(file.clone(),"test-only").unwrap(); }
        assert!(!file.exists());
    }
    #[test]
    fn committed_activation_survives_drop() {
        let dir=tempfile::tempdir().unwrap();let file=dir.path().join("profile.json");
        ProfileChange::at(file.clone(),"test-only").unwrap().commit();
        assert!(file.is_file());
    }
}
