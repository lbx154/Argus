// A private, delete-on-drop diagnostic spool, never exported as a log. Root
// stderr writes to a regular append-only file, so root exit makes its writes
// available without waiting for any descendant's inherited pipe EOF.
use std::{fs::OpenOptions, io::{Read, Seek, SeekFrom}, os::windows::fs::OpenOptionsExt, process::Stdio};
use windows_sys::Win32::Storage::FileSystem::{FILE_ATTRIBUTE_TEMPORARY, FILE_FLAG_DELETE_ON_CLOSE};

pub struct StderrCapture(tempfile::NamedTempFile);
impl StderrCapture {
    pub fn new() -> std::io::Result<Self> { tempfile::NamedTempFile::new().map(Self) }
    pub fn child_stdio(&self) -> std::io::Result<Stdio> {
        // Independent append handle: reading the parent cursor cannot change
        // where a still-running descendant writes. Default Windows sharing
        // allows deletion when the parent drops the private temporary name.
        OpenOptions::new().append(true).custom_flags(FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_DELETE_ON_CLOSE)
            .open(self.0.path()).map(Stdio::from)
    }
    pub fn snapshot(&mut self) -> std::io::Result<Vec<String>> {
        let file = self.0.as_file_mut();
        let end = file.metadata()?.len();
        let start = end.saturating_sub(64 * 1024);
        file.seek(SeekFrom::Start(start))?;
        let mut bytes = Vec::new();
        file.take(end - start).read_to_end(&mut bytes)?;
        // Bound by the captured file length, not an EOF a descendant can keep
        // moving. Drop a partial first line when taking a bounded tail.
        if start > 0 {
            if let Some(newline) = bytes.iter().position(|byte| *byte == b'\n') { bytes.drain(..=newline); }
            else { bytes.clear(); }
        }
        Ok(String::from_utf8_lossy(&bytes).lines().map(str::to_owned).collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::{io::{AsyncBufReadExt, BufReader}, process::Command};
    #[tokio::test]
    async fn a_single_root_error_is_readable_after_its_append_handle_closes() {
        let mut capture = StderrCapture::new().unwrap();
        let mut child = Command::new(crate::runner::find_node(None).unwrap())
            .args(["-e", "process.stderr.write('single root error\\n'); process.exitCode=7;"])
            .stderr(capture.child_stdio().unwrap()).spawn().unwrap();
        assert_eq!(child.wait().await.unwrap().code(), Some(7));
        assert!(capture.snapshot().unwrap().iter().any(|line| line == "single root error"));
    }

    #[tokio::test]
    async fn private_spool_is_deleted_even_without_the_parent_path_destructor() {
        let capture = StderrCapture::new().unwrap();
        let path = capture.0.path().to_path_buf();
        let mut child = Command::new(crate::runner::find_node(None).unwrap())
            .args(["-e", "console.log('ready'); setInterval(()=>{},1000)"])
            .stdout(Stdio::piped()).stderr(capture.child_stdio().unwrap()).kill_on_drop(true).spawn().unwrap();
        assert_eq!(BufReader::new(child.stdout.take().unwrap()).lines().next_line().await.unwrap().unwrap(), "ready");
        let (parent, name) = capture.0.into_parts();
        // Simulate process death: handles close but no NamedTempFile/TempPath
        // destructor can unlink the name. The inherited writer flag must do it.
        std::mem::forget(name); drop(parent);
        child.kill().await.unwrap();
        let gone = !path.exists();
        if !gone { let _ = std::fs::remove_file(&path); }
        assert!(gone, "delete-on-close must not depend on the desktop destructor");
    }

    #[tokio::test]
    async fn root_exit_diagnostic_does_not_wait_for_an_inherited_writer() {
        let mut capture = StderrCapture::new().unwrap();
        let node = crate::runner::find_node(None).unwrap();
        let mut child = Command::new(node).args(["-e", "const c=require('child_process').spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'inherit',detached:true}); c.unref(); console.log(c.pid); process.stderr.write('EADDRINUSE fixture reason before exit\\n',()=>process.exit(7));"])
            .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(capture.child_stdio().unwrap()).kill_on_drop(true).spawn().unwrap();
        let descendant: u32 = BufReader::new(child.stdout.take().unwrap()).lines().next_line().await.unwrap().unwrap().parse().unwrap();
        let code = child.wait().await.unwrap().code();
        let reason = capture.snapshot().unwrap();
        let writer_still_alive = crate::process::is_process_alive(descendant);
        crate::process::terminate_process_tree(descendant).await.unwrap();
        assert_eq!(code, Some(7));
        assert!(writer_still_alive);
        assert!(reason.iter().any(|line| line.contains("EADDRINUSE fixture reason before exit")));
        let status = crate::models::BackendStatus { state: crate::models::BackendState::Error,
            message: "端口已被占用，请更换端口。".into(), detail: Some(reason.join("\n")), ..Default::default() };
        let consumer = crate::setup_failure(&status);
        assert!(consumer.contains("端口已被占用") && consumer.contains("EADDRINUSE fixture reason before exit"));
        let path = capture.0.path().to_path_buf();
        drop(capture);
        assert!(!path.exists(), "private raw stderr is not a persistent log");
    }
}
