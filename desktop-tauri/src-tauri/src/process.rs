#[cfg(windows)]
use std::os::windows::process::CommandExt as _;
use std::{
    process::Stdio,
    time::{Duration, Instant},
};
use tokio::{process::Command, time::sleep};

// The Tauri host is a GUI process. Keep its maintenance commands invisible just
// like the backend and runner preflight, including infrequent recovery paths.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[cfg(windows)]
pub fn is_process_alive(pid: u32) -> bool {
    use windows_sys::Win32::{
        Foundation::CloseHandle,
        System::Threading::{OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION},
    };
    if pid == 0 {
        return false;
    }
    // Query-only access treats access denied as "alive", so a PID is never
    // considered dead merely because this process lacks rights to signal it.
    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle.is_null() {
        // A protected process can reject query access. tasklist is only a
        // liveness fallback; no kill decision is made unless ownership is
        // already authenticated by the supervisor.
        let mut command = std::process::Command::new("tasklist");
        command.creation_flags(CREATE_NO_WINDOW);
        return command
            .args(["/FI", &format!("PID eq {pid}"), "/NH"])
            .output()
            .ok()
            .is_some_and(|output| {
                String::from_utf8_lossy(&output.stdout).contains(&pid.to_string())
            });
    }
    unsafe { CloseHandle(handle) };
    true
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

/// Terminate one proven Windows process tree and verify the root actually died.
pub async fn terminate_windows_process_tree(pid: u32) -> bool {
    if pid == 0 || !is_process_alive(pid) {
        return pid != 0;
    }
    #[cfg(windows)]
    {
        let mut command = Command::new("taskkill");
        command.creation_flags(CREATE_NO_WINDOW);
        let _ = command
            .args(["/pid", &pid.to_string(), "/t", "/f"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
    }
    #[cfg(not(windows))]
    {
        let _ = Command::new("kill")
            .args(["-TERM", &pid.to_string()])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    while is_process_alive(pid) && Instant::now() < deadline {
        sleep(Duration::from_millis(50)).await;
    }
    !is_process_alive(pid)
}

#[cfg(test)]
mod tests {
    #[test]
    fn zero_is_never_a_live_or_killable_pid() {
        assert!(!super::is_process_alive(0));
    }
}
