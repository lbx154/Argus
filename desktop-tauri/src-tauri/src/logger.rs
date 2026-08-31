use crate::redaction::redact_sensitive_text;
use chrono::Utc;
use std::{
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

const MAX_LOG_BYTES: u64 = 5 * 1024 * 1024;

#[derive(Clone)]
pub struct DesktopLogger {
    path: PathBuf,
    file: Arc<Mutex<File>>,
}

impl DesktopLogger {
    pub fn new(data_dir: &Path) -> anyhow::Result<Self> {
        let logs = data_dir.join("logs");
        fs::create_dir_all(&logs)?;
        let path = logs.join("desktop.log");
        rotate_if_needed(&path)?;
        let file = OpenOptions::new().create(true).append(true).open(&path)?;
        Ok(Self {
            path,
            file: Arc::new(Mutex::new(file)),
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn info(&self, message: impl AsRef<str>) {
        self.write("INFO", message.as_ref());
    }

    pub fn warn(&self, message: impl AsRef<str>) {
        self.write("WARN", message.as_ref());
    }

    pub fn error(&self, message: impl AsRef<str>) {
        self.write("ERROR", message.as_ref());
    }

    pub fn verbose(&self, message: impl AsRef<str>) {
        self.write("DEBUG", message.as_ref());
    }

    fn write(&self, level: &str, message: &str) {
        let safe = redact_sensitive_text(message);
        let line = format!(
            "{} [{}] {}\n",
            Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
            level,
            safe
        );
        if let Ok(mut file) = self.file.lock() {
            let _ = file.write_all(line.as_bytes());
            let _ = file.flush();
        }
    }
}

fn rotate_if_needed(path: &Path) -> anyhow::Result<()> {
    if path.metadata().map(|meta| meta.len()).unwrap_or(0) < MAX_LOG_BYTES {
        return Ok(());
    }
    let prior = path.with_extension("log.1");
    let _ = fs::remove_file(&prior);
    fs::rename(path, prior)?;
    Ok(())
}
