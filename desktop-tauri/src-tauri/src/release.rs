use crate::models::{BackendStatus, DesktopReleaseIdentity, DesktopRuntimeIdentity};
use serde::Deserialize;
use std::{env, fs, path::{Path, PathBuf}, sync::OnceLock};

// The host's expected identity is part of the executable, not a file reread on
// every heartbeat. A disappearing/replaced manifest is a package error, never
// evidence that an already authenticated listener changed its identity.
const BUILD_MANIFEST: &str = include_str!("../../../argus_skill/release_manifest.json");

#[derive(Clone)]
pub struct ReleaseContext {
    pub development: bool,
    pub app_version: String,
    pub repo_root: PathBuf,
    pub resource_dir: Option<PathBuf>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
struct ReleaseManifest {
    package_version: String,
    release_id: String,
    source_digest: String,
}

fn parse_manifest(raw: &str) -> Result<ReleaseManifest, String> {
    let manifest: ReleaseManifest = serde_json::from_str(raw)
        .map_err(|error| format!("版本清单格式无效：{error}"))?;
    if manifest.package_version.is_empty() || manifest.release_id.is_empty()
        || manifest.source_digest.len() != 64
        || !manifest.source_digest.bytes().all(|byte| byte.is_ascii_hexdigit())
    {
        return Err("版本清单缺少有效的版本或源码摘要。".into());
    }
    Ok(manifest)
}

fn build_manifest() -> &'static ReleaseManifest {
    static MANIFEST: OnceLock<ReleaseManifest> = OnceLock::new();
    MANIFEST.get_or_init(|| parse_manifest(BUILD_MANIFEST).expect("build must contain a valid Argus release manifest"))
}

impl ReleaseContext {
    pub fn manifest_path(&self) -> Option<PathBuf> {
        if self.development {
            return Some(self.repo_root.join("argus_skill/release_manifest.json"));
        }
        self.resource_dir.as_ref().map(|resources|
            resources.join("argus-backend/_internal/argus_skill/release_manifest.json"))
    }

    pub fn backend_executable(&self) -> Option<PathBuf> {
        if self.development {
            return None;
        }
        self.resource_dir.as_ref().map(|resources| {
            resources.join("argus-backend").join(if cfg!(windows) {
                "argus-backend.exe"
            } else {
                "argus-backend"
            })
        })
    }

    pub fn identity(&self) -> DesktopReleaseIdentity {
        let manifest = build_manifest();
        DesktopReleaseIdentity {
            package_version: manifest.package_version.clone(),
            release_id: manifest.release_id.clone(),
            source_digest: manifest.source_digest.clone(),
            distribution: if self.development { "development" }
                else if preview_mode() { "preview" } else { "packaged" }.into(),
        }
    }

    pub fn manifest_digest(&self) -> String {
        build_manifest().source_digest.clone()
    }

    /// Check disk resources before spawning/restarting. During a healthy
    /// connection a failure is reported separately, without changing trust in
    /// the authenticated process or terminating the operator's running work.
    pub fn validate_payload(&self) -> Result<(), String> {
        let path = self.manifest_path().ok_or("无法定位内置后端目录。")?;
        let raw = fs::read_to_string(&path).map_err(|error| format!(
            "无法读取运行包版本清单 {}：{error}。请保留完整解压目录，不要在运行时移动、删除或覆盖它。", path.display()))?;
        let actual = parse_manifest(&raw).map_err(|error| format!("{error} 路径：{}", path.display()))?;
        if &actual != build_manifest() || actual.package_version != self.app_version {
            return Err(format!("运行包文件与当前 Argus 程序不配套（{}）。请将同一预览包完整解压到新目录后重新启动。", path.display()));
        }
        if let Some(executable) = self.backend_executable() {
            if !executable.is_file() {
                return Err(format!("内置后端文件缺失：{}。请重新完整解压预览包。", executable.display()));
            }
        }
        Ok(())
    }
}

pub const fn preview_mode() -> bool { cfg!(feature = "preview") }

pub fn development_mode() -> bool {
    // A packaged executable must never be redirected into a mutable checkout
    // by a stale Explorer/developer environment variable.
    cfg!(debug_assertions)
}

pub fn repo_root() -> PathBuf {
    env::var_os("ARGUS_DESKTOP_REPO_ROOT").map(PathBuf::from).unwrap_or_else(|| {
        Path::new(env!("CARGO_MANIFEST_DIR")).parent().and_then(Path::parent)
            .unwrap_or_else(|| Path::new(".")).to_path_buf()
    })
}

pub fn runtime_identity(status: &BackendStatus) -> DesktopRuntimeIdentity {
    DesktopRuntimeIdentity {
        state: status.state.clone(),
        pid: status.pid,
        url: status.url.as_deref().and_then(|value| {
            let mut url = url::Url::parse(value).ok()?;
            url.set_query(None);
            url.set_fragment(None);
            Some(url.to_string())
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(root: &Path) -> ReleaseContext {
        let context = ReleaseContext {
            development: false, app_version: build_manifest().package_version.clone(),
            repo_root: root.join("unrelated-repo"), resource_dir: Some(root.to_path_buf()),
        };
        let manifest = context.manifest_path().unwrap();
        fs::create_dir_all(manifest.parent().unwrap()).unwrap();
        fs::write(&manifest, BUILD_MANIFEST).unwrap();
        fs::write(context.backend_executable().unwrap(), b"fixture backend").unwrap();
        context
    }

    #[test]
    fn runtime_summary_does_not_expose_authentication() {
        let status = crate::models::BackendStatus {
            url: Some("http://127.0.0.1:8799/?token=test-only#fragment".into()), ..Default::default()
        };
        assert_eq!(runtime_identity(&status).url.as_deref(), Some("http://127.0.0.1:8799/"));
    }

    #[test]
    fn missing_or_corrupt_disk_manifest_never_changes_host_identity() {
        let directory = tempfile::tempdir().unwrap();
        let context = fixture(directory.path());
        let digest = context.manifest_digest();
        assert!(context.validate_payload().is_ok());
        let path = context.manifest_path().unwrap();
        fs::remove_file(&path).unwrap();
        assert!(context.validate_payload().unwrap_err().contains("无法读取"));
        assert_eq!(context.manifest_digest(), digest);
        fs::write(&path, "{partial").unwrap();
        assert!(context.validate_payload().unwrap_err().contains("格式无效"));
        assert_eq!(context.identity().source_digest, digest);
        fs::write(&path, BUILD_MANIFEST).unwrap();
        assert!(context.validate_payload().is_ok());
    }

    #[test]
    fn a_replaced_payload_cannot_start_under_an_old_host() {
        let directory = tempfile::tempdir().unwrap();
        let context = fixture(directory.path());
        let mut value: serde_json::Value = serde_json::from_str(BUILD_MANIFEST).unwrap();
        value["source_digest"] = "0".repeat(64).into();
        fs::write(context.manifest_path().unwrap(), value.to_string()).unwrap();
        assert!(context.validate_payload().unwrap_err().contains("不配套"));
        assert_ne!(context.manifest_digest(), "0".repeat(64));
    }

    #[test]
    fn invalid_identity_fields_are_rejected() {
        for raw in ["{}", "[]", r#"{"package_version":"1","release_id":"r","source_digest":"abc"}"#] {
            assert!(parse_manifest(raw).is_err());
        }
    }
}
