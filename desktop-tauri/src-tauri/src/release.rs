use crate::models::{BackendStatus, DesktopReleaseIdentity, DesktopRuntimeIdentity};
use std::{path::PathBuf, sync::OnceLock};

// The Argus this desktop ships: the npm package packages/core, carried as a
// resource directory `argus/` with its layout (dist/argus.mjs beside prompts/,
// ways/ and web/). Its version is part of the executable.
const CORE_PACKAGE: &str = include_str!("../../../packages/core/package.json");

#[derive(Clone)]
pub struct ReleaseContext {
    pub development: bool,
    pub app_version: String,
    pub resource_dir: Option<PathBuf>,
}

fn core_version() -> &'static str {
    static VERSION: OnceLock<String> = OnceLock::new();
    VERSION.get_or_init(|| {
        serde_json::from_str::<serde_json::Value>(CORE_PACKAGE).ok()
            .and_then(|package| package["version"].as_str().map(str::to_owned))
            .expect("packages/core/package.json names a version")
    })
}

impl ReleaseContext {
    /// The program Node runs. Tauri copies the resources beside the
    /// executable in development builds too, so both use the same path.
    pub fn argus_script(&self) -> Option<PathBuf> {
        self.resource_dir.as_ref().map(|resources| resources.join("argus").join("dist").join("argus.mjs"))
    }

    pub fn identity(&self) -> DesktopReleaseIdentity {
        DesktopReleaseIdentity {
            package_version: core_version().to_owned(),
            distribution: if self.development { "development" }
                else if preview_mode() { "preview" } else { "packaged" }.into(),
        }
    }

    /// The shipped Argus, checked on disk before it is started.
    pub fn validate_payload(&self) -> Result<PathBuf, String> {
        let script = self.argus_script().ok_or("无法定位内置后端目录。")?;
        if !script.is_file() {
            return Err(format!("内置后端文件缺失：{}。请重新安装。", script.display()));
        }
        Ok(script)
    }
}

pub const fn preview_mode() -> bool { cfg!(feature = "preview") }

pub fn development_mode() -> bool {
    cfg!(debug_assertions)
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

    #[test]
    fn runtime_summary_does_not_expose_authentication() {
        let status = crate::models::BackendStatus {
            url: Some("http://127.0.0.1:8799/?token=test-only#fragment".into()), ..Default::default()
        };
        assert_eq!(runtime_identity(&status).url.as_deref(), Some("http://127.0.0.1:8799/"));
    }

    #[test]
    fn the_payload_is_the_core_package_layout() {
        let directory = tempfile::tempdir().unwrap();
        let context = ReleaseContext {
            development: false, app_version: "42.0.0".into(), resource_dir: Some(directory.path().to_path_buf()),
        };
        assert!(context.validate_payload().unwrap_err().contains("缺失"));
        let script = directory.path().join("argus/dist/argus.mjs");
        std::fs::create_dir_all(script.parent().unwrap()).unwrap();
        std::fs::write(&script, b"// argus").unwrap();
        assert_eq!(context.validate_payload().unwrap(), script);
        // The desktop's own version is for its updates; the identity is the core's.
        assert_eq!(context.identity().package_version, core_version());
        assert!(!core_version().is_empty());
    }
}
