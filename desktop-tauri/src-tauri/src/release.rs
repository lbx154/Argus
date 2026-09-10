use crate::models::{BackendStatus, DesktopReleaseIdentity, DesktopRuntimeIdentity};
use std::{env, path::{Path, PathBuf}};

#[derive(Clone)]
pub struct ReleaseContext {
    pub development: bool,
    pub app_version: String,
    pub repo_root: PathBuf,
    pub resource_dir: Option<PathBuf>,
}

impl ReleaseContext {
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

    /// The desktop identifies itself by its application version alone; the
    /// backend it starts is trusted through the launch nonce, process identity
    /// and token, never through a separately maintained source fingerprint.
    pub fn identity(&self) -> DesktopReleaseIdentity {
        DesktopReleaseIdentity {
            package_version: self.app_version.clone(),
            distribution: if self.development { "development" }
                else if preview_mode() { "preview" } else { "packaged" }.into(),
        }
    }

    /// Check disk resources before spawning/restarting. During a healthy
    /// connection a failure is reported separately, without changing trust in
    /// the authenticated process or terminating the operator's running work.
    pub fn validate_payload(&self) -> Result<(), String> {
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
    use std::fs;

    fn fixture(root: &Path) -> ReleaseContext {
        let context = ReleaseContext {
            development: false, app_version: "0.1.0".into(),
            repo_root: root.join("unrelated-repo"), resource_dir: Some(root.to_path_buf()),
        };
        let executable = context.backend_executable().unwrap();
        fs::create_dir_all(executable.parent().unwrap()).unwrap();
        fs::write(&executable, b"fixture backend").unwrap();
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
    fn identity_follows_the_application_version() {
        let directory = tempfile::tempdir().unwrap();
        let context = fixture(directory.path());
        assert_eq!(context.identity().package_version, "0.1.0");
        assert_ne!(context.identity().distribution, "development");
    }

    #[test]
    fn a_missing_bundled_backend_is_a_visible_package_error() {
        let directory = tempfile::tempdir().unwrap();
        let context = fixture(directory.path());
        assert!(context.validate_payload().is_ok());
        fs::remove_file(context.backend_executable().unwrap()).unwrap();
        assert!(context.validate_payload().unwrap_err().contains("内置后端文件缺失"));
    }
}
