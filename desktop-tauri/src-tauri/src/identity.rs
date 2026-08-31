use crate::models::{BackendOwnership, ProbeIdentity};
use chrono::{DateTime, Utc};

#[derive(Clone, Debug)]
pub struct ExpectedBackendIdentity {
    pub host: String,
    pub port: u16,
    pub executable: String,
    pub manifest_source_digest: String,
    pub token_sha256: String,
}

#[derive(Clone, Debug)]
pub struct ExpectedBackendLaunch {
    pub launch_nonce: String,
    pub manifest_source_digest: String,
    pub spawned_at_ms: i64,
    pub now_ms: i64,
}

#[derive(Clone, Debug)]
pub struct ExpectedPriorBackendOwnership {
    pub host: String,
    pub port: u16,
    pub token_sha256: String,
}

/// Canonicalize Windows comparison spelling without changing the path's target.
/// Rust's `std::fs::canonicalize` returns the `\\\\?\\` extended form, while
/// Python's `sys.executable` reports an ordinary drive path. They identify the
/// same file and must not make a fresh desktop-owned backend look foreign.
pub fn normalized_windows_path(value: &str) -> String {
    let normalized = value.trim().replace('/', "\\");
    let without_extended_prefix = normalized
        .strip_prefix("\\\\?\\")
        .unwrap_or(normalized.as_str());
    if without_extended_prefix
        .get(..4)
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case("UNC\\"))
    {
        format!("\\\\{}", &without_extended_prefix[4..])
    } else {
        without_extended_prefix.to_owned()
    }
}

pub fn same_path(left: &str, right: &str) -> bool {
    normalized_windows_path(left).eq_ignore_ascii_case(&normalized_windows_path(right))
}

pub fn backend_launch_claim_matches(
    probe: &ProbeIdentity,
    expected: &ExpectedBackendLaunch,
) -> bool {
    let started_at_ms = probe
        .started_at
        .as_deref()
        .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
        .map(|value| value.with_timezone(&Utc).timestamp_millis());
    probe.compatible
        && !probe.occupied
        && probe.pid.filter(|pid| *pid > 0).is_some()
        && probe
            .executable
            .as_deref()
            .is_some_and(|value| !value.is_empty())
        && probe.launch_nonce.as_deref() == Some(expected.launch_nonce.as_str())
        && probe.manifest_source_digest.as_deref() == Some(expected.manifest_source_digest.as_str())
        && started_at_ms.is_some_and(|value| {
            value >= expected.spawned_at_ms - 5_000 && value <= expected.now_ms + 5_000
        })
}

pub fn backend_ownership_matches(
    ownership: &BackendOwnership,
    probe: &ProbeIdentity,
    expected: &ExpectedBackendIdentity,
) -> bool {
    ownership.schema == 3
        && ownership.pid == probe.pid.unwrap_or_default()
        && ownership.root_pid > 0
        && ownership.host == expected.host
        && ownership.port == expected.port
        && same_path(&ownership.executable, &expected.executable)
        && probe
            .executable
            .as_deref()
            .is_some_and(|value| same_path(value, &expected.executable))
        && ownership.manifest_source_digest == expected.manifest_source_digest
        && probe.manifest_source_digest.as_deref() == Some(expected.manifest_source_digest.as_str())
        && ownership.token_sha256 == expected.token_sha256
        && !ownership.started_at.is_empty()
        && probe.started_at.as_deref() == Some(ownership.started_at.as_str())
}

pub fn prior_backend_ownership_matches(
    ownership: &BackendOwnership,
    probe: &ProbeIdentity,
    expected: &ExpectedPriorBackendOwnership,
) -> bool {
    ownership.schema == 3
        && probe.occupied
        && ownership.pid == probe.pid.unwrap_or_default()
        && ownership.root_pid > 0
        && ownership.host == expected.host
        && ownership.port == expected.port
        && !ownership.executable.is_empty()
        && probe
            .executable
            .as_deref()
            .is_some_and(|value| same_path(value, &ownership.executable))
        && !ownership.manifest_source_digest.is_empty()
        && probe.manifest_source_digest.as_deref()
            == Some(ownership.manifest_source_digest.as_str())
        && ownership.token_sha256 == expected.token_sha256
        && !ownership.started_at.is_empty()
        && probe.started_at.as_deref() == Some(ownership.started_at.as_str())
}

pub fn authenticated_bundled_backend_matches(probe: &ProbeIdentity, executable: &str) -> bool {
    probe.occupied
        && probe.authenticated
        && probe.pid.filter(|pid| *pid > 0).is_some()
        && probe
            .executable
            .as_deref()
            .is_some_and(|value| same_path(value, executable))
        && probe
            .manifest_source_digest
            .as_deref()
            .is_some_and(|value| !value.is_empty())
        && probe
            .started_at
            .as_deref()
            .is_some_and(|value| !value.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::ProbeIdentity;

    fn probe() -> ProbeIdentity {
        ProbeIdentity {
            compatible: true,
            occupied: false,
            authenticated: true,
            detail: None,
            pid: Some(4242),
            executable: Some("D:\\Argus\\argus-backend.exe".into()),
            manifest_source_digest: Some("a".repeat(64)),
            started_at: Some("2026-08-09T13:00:00Z".into()),
            launch_nonce: Some("nonce".into()),
            failure_kind: None,
        }
    }

    #[test]
    fn exact_owned_backend_matches() {
        let ownership = BackendOwnership {
            schema: 3,
            pid: 4242,
            root_pid: 4141,
            host: "127.0.0.1".into(),
            port: 8799,
            executable: "D:\\Argus\\argus-backend.exe".into(),
            manifest_source_digest: "a".repeat(64),
            token_sha256: "b".repeat(64),
            started_at: "2026-08-09T13:00:00Z".into(),
        };
        let expected = ExpectedBackendIdentity {
            host: "127.0.0.1".into(),
            port: 8799,
            executable: ownership.executable.clone(),
            manifest_source_digest: ownership.manifest_source_digest.clone(),
            token_sha256: ownership.token_sha256.clone(),
        };
        assert!(backend_ownership_matches(&ownership, &probe(), &expected));
        assert!(!backend_ownership_matches(
            &BackendOwnership {
                pid: 1,
                ..ownership
            },
            &probe(),
            &expected,
        ));
    }

    #[test]
    fn accepts_python_drive_paths_after_rust_canonicalization() {
        assert_eq!(
            normalized_windows_path(r"\\?\D:\Argus\argus-backend.exe"),
            r"D:\Argus\argus-backend.exe"
        );
        assert_eq!(
            normalized_windows_path(r"\\?\UNC\server\share\argus-backend.exe"),
            r"\\server\share\argus-backend.exe"
        );
        let ownership = BackendOwnership {
            schema: 3,
            pid: 4242,
            root_pid: 4141,
            host: "127.0.0.1".into(),
            port: 8799,
            executable: r"\\?\D:\Argus\argus-backend.exe".into(),
            manifest_source_digest: "a".repeat(64),
            token_sha256: "b".repeat(64),
            started_at: "2026-08-09T13:00:00Z".into(),
        };
        let expected = ExpectedBackendIdentity {
            host: "127.0.0.1".into(),
            port: 8799,
            executable: r"D:\Argus\argus-backend.exe".into(),
            manifest_source_digest: ownership.manifest_source_digest.clone(),
            token_sha256: ownership.token_sha256.clone(),
        };
        assert!(backend_ownership_matches(&ownership, &probe(), &expected));
    }

    #[test]
    fn launch_claim_binds_nonce_digest_and_start_time() {
        let expected = ExpectedBackendLaunch {
            launch_nonce: "nonce".into(),
            manifest_source_digest: "a".repeat(64),
            spawned_at_ms: DateTime::parse_from_rfc3339("2026-08-09T12:59:59Z")
                .unwrap()
                .timestamp_millis(),
            now_ms: DateTime::parse_from_rfc3339("2026-08-09T13:00:01Z")
                .unwrap()
                .timestamp_millis(),
        };
        assert!(backend_launch_claim_matches(&probe(), &expected));
        let mut stale = probe();
        stale.launch_nonce = Some("other".into());
        assert!(!backend_launch_claim_matches(&stale, &expected));
    }
}
