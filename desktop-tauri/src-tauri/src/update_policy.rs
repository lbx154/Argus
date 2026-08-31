//! Pure update-presentation policy kept independent of the WebView/Tauri host.
//!
//! The updater transport decides whether a manifest represents an update; this
//! module adds the product rule that automatic UI must only surface a strictly
//! newer semantic version and must respect an operator dismissal.

use semver::Version;
use serde::{Deserialize, Serialize};

#[derive(Default, Deserialize, Serialize)]
#[serde(default, rename_all = "camelCase")]
pub(crate) struct UpdateCache {
    /// Last successful manifest check. Retained under its original field name
    /// for backwards-compatible cache migration.
    pub(crate) last_checked_at: Option<i64>,
    pub(crate) last_attempt_at: Option<i64>,
    pub(crate) consecutive_failures: u32,
    pub(crate) dismissed_version: Option<String>,
    /// A native toast is deliberately sent once per candidate version, not on
    /// every app launch while the user is still deciding.
    pub(crate) announced_version: Option<String>,
    /// Preserve a known newer release across a restart so the quiet six-hour
    /// throttle does not make the update card disappear until the next poll.
    pub(crate) available_version: Option<String>,
    pub(crate) available_notes: Option<String>,
}

fn parse_version(value: &str) -> Option<Version> {
    Version::parse(value.trim().trim_start_matches('v')).ok()
}

/// Never treat an equal, lower, or malformed version as an automatic update.
pub fn is_strictly_newer(candidate: &str, current: &str) -> bool {
    match (parse_version(candidate), parse_version(current)) {
        (Some(candidate), Some(current)) => candidate > current,
        _ => false,
    }
}

/// Whether a cached candidate should appear in the desktop shell on startup.
pub fn should_present_cached_update(
    candidate: &str,
    current: &str,
    dismissed_version: Option<&str>,
) -> bool {
    dismissed_version != Some(candidate) && is_strictly_newer(candidate, current)
}

/// Exponential retry delay for transient automatic-check failures.
pub fn automatic_retry_delay_seconds(
    consecutive_failures: u32,
    base_seconds: i64,
    max_seconds: i64,
) -> i64 {
    let shift = consecutive_failures.saturating_sub(1).min(4);
    base_seconds.saturating_mul(1_i64 << shift).min(max_seconds)
}

/// Decide whether a silent automatic network check is due.
///
/// Successful checks use the long freshness interval. Failed/interrupted
/// checks retry on a shorter bounded backoff instead of suppressing updates for
/// the full success interval.
pub fn automatic_check_due(
    now: i64,
    last_success_at: Option<i64>,
    last_attempt_at: Option<i64>,
    consecutive_failures: u32,
    success_interval_seconds: i64,
    retry_base_seconds: i64,
    retry_max_seconds: i64,
) -> bool {
    if last_success_at.is_some_and(|then| now.saturating_sub(then) < success_interval_seconds) {
        return false;
    }
    if let Some(then) = last_attempt_at {
        let retry_after = automatic_retry_delay_seconds(
            consecutive_failures,
            retry_base_seconds,
            retry_max_seconds,
        );
        if now.saturating_sub(then) < retry_after {
            return false;
        }
    }
    true
}

#[cfg(test)]
mod tests {
    use super::{
        automatic_check_due, automatic_retry_delay_seconds, is_strictly_newer,
        should_present_cached_update, UpdateCache,
    };

    #[test]
    fn only_a_strictly_newer_semver_can_be_an_automatic_update() {
        assert!(is_strictly_newer("0.1.2", "0.1.1"));
        assert!(!is_strictly_newer("0.1.1", "0.1.1"));
        assert!(!is_strictly_newer("0.1.0", "0.1.1"));
        assert!(!is_strictly_newer("not-a-version", "0.1.1"));
    }

    #[test]
    fn dismissed_or_stale_cached_candidates_are_not_presented() {
        assert!(should_present_cached_update("0.1.2", "0.1.1", None));
        assert!(!should_present_cached_update(
            "0.1.2",
            "0.1.1",
            Some("0.1.2")
        ));
        assert!(!should_present_cached_update("0.1.1", "0.1.1", None));
    }

    #[test]
    fn automatic_failures_retry_before_the_success_freshness_window() {
        assert_eq!(automatic_retry_delay_seconds(1, 900, 7_200), 900);
        assert_eq!(automatic_retry_delay_seconds(4, 900, 7_200), 7_200);
        assert!(!automatic_check_due(
            10_600,
            None,
            Some(10_000),
            1,
            21_600,
            900,
            7_200,
        ));
        assert!(automatic_check_due(
            10_901,
            None,
            Some(10_000),
            1,
            21_600,
            900,
            7_200,
        ));
    }

    #[test]
    fn a_recent_success_suppresses_redundant_network_checks() {
        assert!(!automatic_check_due(
            20_000,
            Some(19_000),
            Some(19_000),
            0,
            21_600,
            900,
            7_200,
        ));
        assert!(automatic_check_due(
            40_601,
            Some(19_000),
            Some(19_000),
            0,
            21_600,
            900,
            7_200,
        ));
    }

    #[test]
    fn legacy_cache_keeps_available_and_dismissed_versions() {
        let cache: UpdateCache = serde_json::from_str(
            r#"{
                "lastCheckedAt": 42,
                "dismissedVersion": "0.1.2",
                "availableVersion": "0.1.3"
            }"#,
        )
        .expect("legacy cache should migrate through serde defaults");

        assert_eq!(cache.last_checked_at, Some(42));
        assert_eq!(cache.last_attempt_at, None);
        assert_eq!(cache.consecutive_failures, 0);
        assert_eq!(cache.dismissed_version.as_deref(), Some("0.1.2"));
        assert_eq!(cache.available_version.as_deref(), Some("0.1.3"));
    }
}
