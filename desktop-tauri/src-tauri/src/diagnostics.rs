use crate::{logger::DesktopLogger, redaction::redact_sensitive_text};
use chrono::Utc;
use rfd::AsyncFileDialog;
use serde_json::json;
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
};
use zip::{write::SimpleFileOptions, CompressionMethod, ZipWriter};

fn safe_read(path: &Path) -> Option<String> {
    fs::read(path).ok().map(|bytes| {
        let start = bytes.len().saturating_sub(500_000);
        String::from_utf8_lossy(&bytes[start..]).into_owned()
    })
}

fn add_text(zip: &mut ZipWriter<fs::File>, name: &str, text: &str) -> anyhow::Result<()> {
    let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    zip.start_file(name, options)?;
    zip.write_all(text.as_bytes())?;
    Ok(())
}

pub async fn export_diagnostics(
    data_dir: PathBuf,
    app_version: String,
    logger: DesktopLogger,
) -> Result<Option<String>, String> {
    let stamp = Utc::now().format("%Y-%m-%dT%H-%M-%S").to_string();
    let Some(file) = AsyncFileDialog::new()
        .set_title("导出 Argus 诊断包")
        .set_file_name(format!("Argus-diagnostics-{stamp}.zip"))
        .add_filter("ZIP", &["zip"])
        .save_file()
        .await
    else {
        return Ok(None);
    };
    let output = file.path().to_path_buf();
    let temporary = data_dir
        .join("temp")
        .join(format!("diagnostics-{}", std::process::id()));
    let result = (|| -> anyhow::Result<()> {
        fs::create_dir_all(&temporary)?;
        let zip_file = fs::File::create(&output)?;
        let mut zip = ZipWriter::new(zip_file);
        if let Some(settings) = safe_read(&data_dir.join("settings.json")) {
            add_text(&mut zip, "settings.json", &redact_sensitive_text(&settings))?;
        }
        if let Some(backend) = safe_read(&data_dir.join("runtime").join("backend.json")) {
            add_text(&mut zip, "backend.json", &redact_sensitive_text(&backend))?;
        }
        if let Some(log) = safe_read(logger.path()) {
            add_text(&mut zip, "desktop.log", &redact_sensitive_text(&log))?;
        }
        let diagnostic = json!({
            "appVersion": app_version,
            "runtime": "Tauri/Rust",
            "platform": std::env::consts::OS,
            "release": std::env::consts::FAMILY,
            "arch": std::env::consts::ARCH,
            "hostname": std::env::var("COMPUTERNAME").unwrap_or_default(),
            "exportedAt": Utc::now().to_rfc3339(),
        });
        add_text(
            &mut zip,
            "diagnostics.json",
            &serde_json::to_string_pretty(&diagnostic)?,
        )?;
        zip.finish()?;
        Ok(())
    })();
    let _ = fs::remove_dir_all(&temporary);
    match result {
        Ok(()) => {
            logger.info(format!("diagnostics exported to {}", output.display()));
            Ok(Some(output.to_string_lossy().into_owned()))
        }
        Err(error) => Err(error.to_string()),
    }
}
