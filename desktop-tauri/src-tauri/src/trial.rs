use crate::{release::ReleaseContext, runner::argus_home_dir};
use serde::{Deserialize, Serialize};
use std::{env, path::PathBuf, process::Stdio, time::Duration};
use tauri::{AppHandle, Emitter};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::Command,
    time::timeout,
};

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TrialSetupInput {
    pub api_key: String,
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
    Complete { runner_bin: String },
    Error { message: String },
}

pub fn valid_key(key: &str) -> bool {
    key.strip_prefix("argus_trial_").is_some_and(|value| {
        value.len() == 64
            && value
                .bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    })
}

pub async fn configure(
    app: &AppHandle,
    release: &ReleaseContext,
    api_key: &str,
) -> Result<String, String> {
    if !valid_key(api_key) {
        return Err("请输入完整的内部测试 Key。".to_owned());
    }
    release.validate_payload()?;
    let executable = if release.development {
        env::var_os("ARGUS_SKILL_BIN")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("python"))
    } else {
        release
            .backend_executable()
            .ok_or("找不到桌面运行环境，请重新安装 Argus。")?
    };
    let mut process = Command::new(executable);
    std::fs::create_dir_all(argus_home_dir()).map_err(|_| "无法创建本地配置目录。")?;
    #[cfg(windows)]
    process.creation_flags(0x0800_0000);
    let mut child = process
        .args(["-m", "argus_skill.trial.desktop"])
        .current_dir(if release.development {
            release.repo_root.clone()
        } else {
            argus_home_dir()
        })
        .env("ARGUS_SKILL_HOME", argus_home_dir())
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .map_err(|_| "无法启动试用配置，请重新安装 Argus。")?;
    // Never place the key in process arguments, settings.json or desktop logs.
    let mut input = child.stdin.take().ok_or("无法创建安全输入通道。")?;
    input
        .write_all(format!("{}\n", serde_json::json!({"api_key": api_key})).as_bytes())
        .await
        .map_err(|_| "无法传入内部测试 Key。")?;
    drop(input);
    let output = child.stdout.take().ok_or("无法读取安装进度。")?;
    let operation = async {
        let mut lines = BufReader::new(output).lines();
        let mut executable = None;
        while let Some(line) = lines.next_line().await.map_err(|_| "读取安装进度失败。")? {
            let event: Event =
                serde_json::from_str(&line).map_err(|_| "安装程序返回了无效结果。")?;
            match event {
                Event::Progress { message } => {
                    let _ = app.emit("argus:trial-progress", message.replace(api_key, "[隐藏]"));
                }
                Event::Download { progress } => {
                    let _ = app.emit("argus:trial-download", progress);
                }
                Event::Complete { runner_bin } => executable = Some(runner_bin),
                Event::Error { message } => return Err(message.replace(api_key, "[隐藏]")),
            }
        }
        let status = child.wait().await.map_err(|_| "无法确认安装状态。")?;
        if !status.success() {
            return Err("试用配置未完成，请重试。".to_owned());
        }
        executable
            .filter(|path| PathBuf::from(path).is_file())
            .ok_or_else(|| "未找到安装好的 Copilot。".to_owned())
    };
    timeout(Duration::from_secs(600), operation)
        .await
        .map_err(|_| "安装超时，请检查网络后重试。")?
}

#[cfg(test)]
mod tests {
    #[test]
    fn forwards_download_bytes_with_optional_total() {
        for total in [serde_json::json!(null), serde_json::json!(2048)] {
            let event = serde_json::json!({"event": "download", "downloaded_bytes": 1024, "total_bytes": total});
            let super::Event::Download { progress } = serde_json::from_value(event).unwrap() else {
                panic!("expected a download event");
            };
            assert_eq!(serde_json::to_value(progress).unwrap(),
                serde_json::json!({"downloaded_bytes": 1024, "total_bytes": total}));
        }
    }

    #[test]
    fn validates_only_trial_keys_without_reflecting_input() {
        assert!(super::valid_key(&format!("argus_trial_{}", "a".repeat(64))));
        for key in [
            "",
            "github-secret",
            "argus_trial_abc",
            &format!("argus_trial_{}", "A".repeat(64)),
        ] {
            assert!(!super::valid_key(key));
        }
    }
}
