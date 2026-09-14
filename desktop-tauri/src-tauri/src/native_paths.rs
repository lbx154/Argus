//! User-mediated paths only; no renderer-supplied initial directory or filesystem reads.
use serde::Deserialize;
use std::sync::atomic::{AtomicBool, Ordering};

#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PathKind {
    Folder,
    Cif,
}

static PICKER_OPEN: AtomicBool = AtomicBool::new(false);

pub struct PickerGuard;
impl PickerGuard {
    pub fn acquire() -> Result<Self, String> {
        PICKER_OPEN.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .map(|_| Self)
            .map_err(|_| "已有文件选择窗口，请先完成或取消。".to_owned())
    }
}
impl Drop for PickerGuard {
    fn drop(&mut self) { PICKER_OPEN.store(false, Ordering::SeqCst); }
}

pub fn valid_selection(kind: PathKind, path: &std::path::Path) -> bool {
    match kind {
        PathKind::Folder => path.is_dir(),
        PathKind::Cif => path.is_file() && path.extension()
            .is_some_and(|extension| extension.to_string_lossy().eq_ignore_ascii_case("cif")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_explicit_supported_dialog_kinds_are_accepted() {
        assert!(matches!(serde_json::from_str::<PathKind>("\"folder\"").unwrap(), PathKind::Folder));
        assert!(serde_json::from_str::<PathKind>("\"file:///C:/arbitrary\"").is_err());
        assert!(serde_json::from_str::<PathKind>("\"shell\"").is_err());
    }

    #[test]
    fn concurrent_dialogs_are_refused_and_cancellation_releases_the_guard() {
        let first = PickerGuard::acquire().unwrap();
        assert!(PickerGuard::acquire().is_err());
        drop(first);
        assert!(PickerGuard::acquire().is_ok());
    }

    #[test]
    fn selection_type_is_validated_without_reading_the_file() {
        let directory = tempfile::tempdir().unwrap();
        let cif = directory.path().join("中文 sample.CIF");
        std::fs::write(&cif, "data_test\n").unwrap();
        assert!(valid_selection(PathKind::Folder, directory.path()));
        assert!(valid_selection(PathKind::Cif, &cif));
        assert!(!valid_selection(PathKind::Folder, &cif));
        assert!(!valid_selection(PathKind::Cif, directory.path()));
    }
}
