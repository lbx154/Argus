#![allow(dead_code)]

// Keep ownership, redaction, resilience, settings and release-identity tests
// executable without constructing a WebView2/Tauri window. The full desktop
// host is separately checked by `cargo check` and packaged in Windows CI.
#[path = "identity.rs"]
mod identity;
#[path = "models.rs"]
mod models;
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
