#![allow(dead_code)]

// Keep redaction, runner, settings and release tests executable without
// constructing a WebView2/Tauri window. The full desktop host is separately
// checked by `cargo check` and `cargo test --lib`.
#[path = "models.rs"]
mod models;
#[path = "redaction.rs"]
mod redaction;
#[path = "release.rs"]
mod release;
#[path = "runner.rs"]
mod runner;
#[path = "settings.rs"]
mod settings;
#[path = "update_install.rs"]
mod update_install;
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
