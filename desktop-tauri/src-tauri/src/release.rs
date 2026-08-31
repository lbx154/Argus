use crate::models::{BackendStatus, DesktopReleaseIdentity, DesktopRuntimeIdentity};
use serde::Deserialize;
use std::{
    env, fs,
    path::{Path, PathBuf},
};

#[derive(Clone)]
pub struct ReleaseContext {
    pub development: bool,
    pub app_version: String,
    pub repo_root: PathBuf,
    pub resource_dir: Option<PathBuf>,
}

#[derive(Deserialize)]
struct ReleaseManifest {
    package_version: Option<String>,
    release_id: Option<String>,
    source_digest: Option<String>,
}

impl ReleaseContext {
    pub fn manifest_path(&self) -> Option<PathBuf> {
        if self.development {
            return Some(
                self.repo_root
                    .join("argus_skill")
                    .join("release_manifest.json"),
            );
        }
        self.resource_dir.as_ref().map(|resources| {
            resources
                .join("argus-backend")
                .join("_internal")
                .join("argus_skill")
                .join("release_manifest.json")
        })
    }

    pub fn backend_executable(&self) -> Option<PathBuf> {
        if self.development {
            return None;
        }
        self.resource_dir
            .as_ref()
            .map(|resources| resources.join("argus-backend").join("argus-backend.exe"))
    }

    pub fn identity(&self) -> DesktopReleaseIdentity {
        let distribution = if self.development {
            "development"
        } else {
            "packaged"
        }
        .to_owned();
        let fallback = || DesktopReleaseIdentity {
            package_version: self.app_version.clone(),
            release_id: format!("{}+manifest-unavailable", self.app_version),
            source_digest: String::new(),
            distribution: distribution.clone(),
        };
        let Some(path) = self.manifest_path() else {
            return fallback();
        };
        let Ok(raw) = fs::read_to_string(path) else {
            return fallback();
        };
        let Ok(manifest) = serde_json::from_str::<ReleaseManifest>(&raw) else {
            return fallback();
        };
        DesktopReleaseIdentity {
            package_version: manifest
                .package_version
                .unwrap_or_else(|| self.app_version.clone()),
            release_id: manifest
                .release_id
                .unwrap_or_else(|| self.app_version.clone()),
            source_digest: manifest.source_digest.unwrap_or_default(),
            distribution,
        }
    }

    pub fn manifest_digest(&self) -> Option<String> {
        let value = self.identity().source_digest;
        (!value.trim().is_empty()).then_some(value)
    }
}

pub fn development_mode() -> bool {
    env::var("ARGUS_DESKTOP_DEV").as_deref() == Ok("1") || cfg!(debug_assertions)
}

pub fn repo_root() -> PathBuf {
    env::var_os("ARGUS_DESKTOP_REPO_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .and_then(Path::parent)
                .unwrap_or_else(|| Path::new("."))
                .to_path_buf()
        })
}

pub fn runtime_identity(status: &BackendStatus) -> DesktopRuntimeIdentity {
    DesktopRuntimeIdentity {
        state: status.state.clone(),
        pid: status.pid,
        url: status.url.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::ReleaseContext;
    use std::fs;

    #[test]
    fn packaged_identity_only_reads_the_bundled_manifest() {
        let root = tempfile::tempdir().unwrap();
        let resource = root.path().join("resources");
        let manifest = resource.join("argus-backend/_internal/argus_skill/release_manifest.json");
        fs::create_dir_all(manifest.parent().unwrap()).unwrap();
        fs::write(
            &manifest,
            r#"{"package_version":"0.1.2","release_id":"r1","source_digest":"abc"}"#,
        )
        .unwrap();
        let context = ReleaseContext {
            development: false,
            app_version: "9.9.9".into(),
            repo_root: root.path().join("repo"),
            resource_dir: Some(resource),
        };
        let identity = context.identity();
        assert_eq!(identity.release_id, "r1");
        assert_eq!(identity.source_digest, "abc");
        assert_eq!(identity.distribution, "packaged");
    }
}
