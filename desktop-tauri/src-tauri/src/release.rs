use crate::models::{BackendStatus, DesktopReleaseIdentity, DesktopRuntimeIdentity};
use serde::Deserialize;
use std::{env, fs, path::{Path, PathBuf}, sync::OnceLock};

// The host's expected identity is part of the executable, not a file reread on
// every heartbeat. A disappearing/replaced manifest is a package error, never
// evidence that an already authenticated listener changed its identity.
const BUILD_MANIFEST: &str = include_str!("../../../argus/release_manifest.json");

#[derive(Clone)]
pub struct ReleaseContext {
    pub development: bool,
    pub app_version: String,
    pub repo_root: PathBuf,
    pub resource_dir: Option<PathBuf>,
}

/// The engine release this desktop build ships: its package version and the
/// release id the build stamps from that version and its git commit.
#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
struct ReleaseManifest {
    package_version: String,
    release_id: String,
    #[serde(default)]
    build_epoch: i64,
}

pub(crate) fn build_epoch() -> i64 { build_manifest().build_epoch }
pub(crate) fn compiled_release_id() -> &'static str { &build_manifest().release_id }

fn parse_manifest(raw: &str) -> Result<ReleaseManifest, String> {
    let manifest: ReleaseManifest = serde_json::from_str(raw)
        .map_err(|error| format!("版本清单格式无效：{error}"))?;
    if manifest.package_version.is_empty() || manifest.release_id.is_empty() {
        return Err("版本清单缺少有效的版本或发布标识。".into());
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
            return Some(self.repo_root.join("argus/release_manifest.json"));
        }
        self.resource_dir.as_ref().map(|resources|
            resources.join("argus-backend/_internal/argus/release_manifest.json"))
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

    /// Backend identity comes from the compiled engine manifest. The desktop's
    /// independent product version is used for application updates, not trust.
    pub fn identity(&self) -> DesktopReleaseIdentity {
        let manifest = build_manifest();
        DesktopReleaseIdentity {
            package_version: manifest.package_version.clone(),
            release_id: manifest.release_id.clone(),
            distribution: if self.development { "development" }
                else if preview_mode() { "preview" } else { "packaged" }.into(),
        }
    }

    pub fn release_id(&self) -> String {
        build_manifest().release_id.clone()
    }

    pub fn validate_backend_release(
        &self,
        package_version: Option<&str>,
        release_id: Option<&str>,
    ) -> Result<(), String> {
        let expected = build_manifest();
        if package_version != Some(expected.package_version.as_str()) {
            return Err(format!(
                "端口上的后端版本为 {}，当前构建要求 {}",
                package_version.unwrap_or_default(), expected.package_version,
            ));
        }
        if release_id != Some(expected.release_id.as_str()) {
            return Err("端口上的后端不是当前桌面构建".to_owned());
        }
        Ok(())
    }

    /// Check disk resources before spawning/restarting. During a healthy
    /// connection a failure is reported separately, without changing trust in
    /// the authenticated process or terminating the operator's running work.
    pub fn validate_payload(&self) -> Result<(), String> {
        let path = self.manifest_path().ok_or("无法定位内置后端目录。")?;
        let raw = fs::read_to_string(&path).map_err(|error| format!(
            "无法读取运行包版本清单 {}：{error}。请保留完整解压目录，不要在运行时移动、删除或覆盖它。", path.display()))?;
        let actual = parse_manifest(&raw).map_err(|error| format!("{error} 路径：{}", path.display()))?;
        if &actual != build_manifest() {
            return Err(format!("运行包文件与当前程序不配套（{}）。请将同一预览包完整解压到新目录后重新启动。", path.display()));
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
        let release = context.release_id();
        assert!(context.validate_payload().is_ok());
        let path = context.manifest_path().unwrap();
        fs::remove_file(&path).unwrap();
        assert!(context.validate_payload().unwrap_err().contains("无法读取"));
        assert_eq!(context.release_id(), release);
        fs::write(&path, "{partial").unwrap();
        assert!(context.validate_payload().unwrap_err().contains("格式无效"));
        assert_eq!(context.identity().release_id, release);
        fs::write(&path, BUILD_MANIFEST).unwrap();
        assert!(context.validate_payload().is_ok());
    }

    #[test]
    fn a_replaced_payload_cannot_start_under_an_old_host() {
        let directory = tempfile::tempdir().unwrap();
        let context = fixture(directory.path());
        let mut value: serde_json::Value = serde_json::from_str(BUILD_MANIFEST).unwrap();
        value["release_id"] = "0.0.0+replaced".into();
        fs::write(context.manifest_path().unwrap(), value.to_string()).unwrap();
        assert!(context.validate_payload().unwrap_err().contains("不配套"));
        assert_ne!(context.release_id(), "0.0.0+replaced");
    }

    #[test]
    fn application_version_is_independent_of_the_bundled_backend_version() {
        let directory = tempfile::tempdir().unwrap();
        let mut context = fixture(directory.path());
        context.app_version = "42.0.0".into();
        assert_ne!(context.app_version, context.identity().package_version);
        assert!(context.validate_payload().is_ok());
        assert_eq!(context.app_version, "42.0.0");
    }

    #[test]
    fn live_backend_uses_engine_version_and_exact_release_identity() {
        let directory = tempfile::tempdir().unwrap();
        let mut context = fixture(directory.path());
        context.app_version = "42.0.0".into();
        let engine = context.identity();
        assert!(context.validate_backend_release(
            Some(&engine.package_version), Some(&engine.release_id),
        ).is_ok());
        for version in [None, Some(""), Some("42.0.0")] {
            assert!(context.validate_backend_release(version, Some(&engine.release_id)).is_err());
        }
        for release in [None, Some(""), Some("0.0.0+other")] {
            assert!(context.validate_backend_release(Some(&engine.package_version), release).is_err());
        }
        assert_eq!(context.app_version, "42.0.0");
    }

    #[test]
    fn matching_product_version_cannot_authorize_a_replaced_backend() {
        let directory = tempfile::tempdir().unwrap();
        let mut context = fixture(directory.path());
        context.app_version = "42.0.0".into();
        let mut value: serde_json::Value = serde_json::from_str(BUILD_MANIFEST).unwrap();
        value["package_version"] = context.app_version.clone().into();
        fs::write(context.manifest_path().unwrap(), value.to_string()).unwrap();
        assert!(context.validate_payload().is_err());
    }

    #[test]
    fn invalid_identity_fields_are_rejected() {
        for raw in ["{}", "[]", r#"{"package_version":"1","release_id":""}"#] {
            assert!(parse_manifest(raw).is_err());
        }
    }
}
