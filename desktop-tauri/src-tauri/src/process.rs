#[cfg(not(windows))]
use std::process::Stdio;
#[cfg(not(windows))]
use std::time::{Duration, Instant};
#[cfg(not(windows))]
use tokio::process::Command;
#[cfg(not(windows))]
use tokio::time::sleep;

#[cfg(windows)]
#[path = "process_windows.rs"]
mod windows;
#[cfg(windows)]
pub use windows::ProcessTree;

#[cfg(all(windows, test))]
pub fn is_process_alive(pid: u32) -> bool {
    use windows_sys::Win32::{
        Foundation::{CloseHandle, GetLastError, ERROR_INVALID_PARAMETER},
        System::Threading::{GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION},
    };
    if pid == 0 { return false; }
    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle.is_null() {
        // Access denied is not evidence that a protected process is dead.
        // No PATH/cwd-dependent tasklist fallback is needed.
        return unsafe { GetLastError() } != ERROR_INVALID_PARAMETER;
    }
    let mut code = 0;
    let running = unsafe { GetExitCodeProcess(handle, &mut code) } == 0 || code == 259;
    unsafe { CloseHandle(handle) };
    running
}

#[cfg(not(windows))]
pub fn is_process_alive(pid: u32) -> bool {
    pid > 0
        && std::process::Command::new("kill")
            .args(["-0", &pid.to_string()])
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
}

/// End the retained backend's tree while its root is still running. Windows
/// taskkill cannot discover descendants after a root has already self-exited.
#[cfg(any(not(windows), test))]
pub async fn terminate_process_tree(pid: u32) -> Result<(), String> {
    if pid == 0 { return Err("Refusing to stop PID 0".into()); }
    if !is_process_alive(pid) { return Ok(()); }
    #[cfg(windows)]
    {
        if let Some(tree) = ProcessTree::open(pid)? { tree.stop().await?; }
        Ok(())
    }
    #[cfg(not(windows))]
    {
        // Preserve the established POSIX signal-and-wait behaviour.
        let _ = Command::new("kill")
            .args(["-TERM", &pid.to_string()])
            .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn();
        let deadline = Instant::now() + Duration::from_secs(5);
        while is_process_alive(pid) && Instant::now() < deadline {
            sleep(Duration::from_millis(50)).await;
        }
        if is_process_alive(pid) { Err(format!("Backend PID {pid} did not exit after SIGTERM")) } else { Ok(()) }
    }
}

#[cfg(test)]
mod tests {
    #[cfg(windows)]
    #[tokio::test]
    async fn stop_reaps_the_owned_tree_but_not_an_unrelated_process() {
        exercise_tree(crate::runner::find_node(None).unwrap()).await;
    }

    #[cfg(windows)]
    async fn exercise_tree(node: std::path::PathBuf) {
        use tokio::{io::{AsyncBufReadExt, BufReader}, process::Command};
        use std::process::Stdio;
        let mut unrelated = Command::new(&node).args(["-e", "setInterval(()=>{},1000)"])
            .kill_on_drop(true).spawn().unwrap();
        let mut parent = Command::new(&node).args(["-e", "const c=require('child_process').spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'}); console.log(c.pid); setInterval(()=>{},1000)"])
            .stdout(Stdio::piped()).kill_on_drop(true).spawn().unwrap();
        let line = BufReader::new(parent.stdout.take().unwrap()).lines().next_line().await.unwrap().unwrap();
        let grandchild: u32 = line.trim().parse().unwrap();
        assert!(super::is_process_alive(grandchild));
        let stopped = super::terminate_process_tree(parent.id().unwrap()).await;
        // Cleanup is by retained fixture PID, even if an assertion fails.
        if stopped.is_err() {
            let cleanup = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap()).join("System32/taskkill.exe");
            let _ = Command::new(cleanup).args(["/PID", &parent.id().unwrap().to_string(), "/T", "/F"]).status().await;
            let _ = parent.kill().await;
        }
        parent.wait().await.unwrap();
        assert!(stopped.is_ok(), "{stopped:?}");
        assert!(!super::is_process_alive(grandchild), "owned descendant survived");
        assert!(super::is_process_alive(unrelated.id().unwrap()), "unrelated process was touched");
        unrelated.kill().await.unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn stop_uses_the_system_command_not_cwd_or_a_minimal_path() {
        let root = tempfile::tempdir().unwrap();
        let node = crate::runner::find_node(None).unwrap();
        // If a bare command were used these Node executables would consume
        // taskkill's arguments, fail, and leave the fixture tree alive.
        std::fs::copy(&node, root.path().join("taskkill.exe")).unwrap();
        std::fs::copy(&node, root.path().join("tasklist.exe")).unwrap();
        for mode in ["system-path", "missing-system-root"] {
            let output = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "process::tests::native_stop_child", "--ignored"])
                .env("ARGUS_TEST_NODE", &node).env("ARGUS_STOP_MODE", mode)
                .env("Path", root.path()).current_dir(root.path()).output().unwrap();
            assert!(output.status.success(), "{mode}: {} {}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        }
    }

    #[cfg(windows)]
    #[tokio::test]
    #[ignore = "isolated environment for stop_uses_the_system_command_not_cwd_or_a_minimal_path"]
    async fn native_stop_child() {
        let node = std::path::PathBuf::from(std::env::var_os("ARGUS_TEST_NODE").unwrap());
        if std::env::var("ARGUS_STOP_MODE").unwrap() == "system-path" { return exercise_tree(node).await; }
        let mut child = tokio::process::Command::new(node).args(["-e", "setInterval(()=>{},1000)"])
            .kill_on_drop(true).spawn().unwrap();
        std::env::remove_var("SystemRoot"); std::env::remove_var("WINDIR");
        let result = super::terminate_process_tree(child.id().unwrap()).await;
        child.kill().await.unwrap();
        assert!(result.unwrap_err().contains("SystemRoot is not set"));
    }

    #[test]
    fn zero_is_never_a_live_or_killable_pid() {
        assert!(!super::is_process_alive(0));
    }
}
