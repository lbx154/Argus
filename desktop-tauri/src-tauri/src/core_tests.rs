#![allow(dead_code)]

// Keep ownership, redaction, resilience, settings and release-identity tests
// executable without constructing a WebView2/Tauri window. The full desktop
// host is separately checked by `cargo check` and packaged in Windows CI.
#[path = "identity.rs"]
mod identity;
#[path = "models.rs"]
mod models;
#[path = "probe.rs"]
mod probe;
#[path = "redaction.rs"]
mod redaction;
#[path = "release.rs"]
mod release;
#[path = "resilience.rs"]
mod resilience;
#[path = "runner.rs"]
mod runner;
#[path = "settings.rs"]
mod settings;
#[path = "update_policy.rs"]
mod update_policy;

#[test]
fn desktop_frame_policy_allows_blob_download_navigation() {
    let config: serde_json::Value =
        serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
    for key in ["csp", "devCsp"] {
        let policy = config["app"]["security"][key].as_str().unwrap();
        let frames = policy.split(';').find(|part| part.trim().starts_with("frame-src ")).unwrap();
        assert!(frames.split_whitespace().any(|source| source == "blob:"));
    }
}
