// Windows desktop process identity and one-request tree termination. No name
// matching, retry, watchdog or Job Object containment is implied by a snapshot.
use std::{collections::BTreeMap, os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle}, path::PathBuf,
    process::Stdio, sync::{Arc, Mutex}, time::Instant};
use windows_sys::Win32::{
    Foundation::{FILETIME, GetLastError, INVALID_HANDLE_VALUE, ERROR_INVALID_PARAMETER, ERROR_NO_MORE_FILES, WAIT_OBJECT_0},
    Globalization::{MultiByteToWideChar, CP_OEMCP},
    System::{Diagnostics::ToolHelp::{CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS},
        Threading::{GetProcessTimes, OpenProcess, WaitForMultipleObjects, WaitForSingleObject, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE}},
};

struct Process { pid: u32, born: u64, handle: OwnedHandle }
impl Process {
    fn open(pid: u32) -> Result<Option<Arc<Self>>, String> {
        let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE, 0, pid) };
        if handle.is_null() {
            return if unsafe { GetLastError() } == ERROR_INVALID_PARAMETER { Ok(None) }
                else { Err(format!("Cannot retain PID {pid}: {}", std::io::Error::last_os_error())) };
        }
        let handle = unsafe { OwnedHandle::from_raw_handle(handle) };
        let (mut created, mut exit, mut kernel, mut user): (FILETIME, FILETIME, FILETIME, FILETIME) = unsafe { std::mem::zeroed() };
        if unsafe { GetProcessTimes(handle.as_raw_handle(), &mut created, &mut exit, &mut kernel, &mut user) } == 0 {
            return Err(format!("Cannot identify PID {pid}: {}", std::io::Error::last_os_error()));
        }
        Ok(Some(Arc::new(Self { pid, born: (u64::from(created.dwHighDateTime) << 32) | u64::from(created.dwLowDateTime), handle })))
    }
    fn alive(&self) -> bool { (unsafe { WaitForSingleObject(self.handle.as_raw_handle(), 0) }) != WAIT_OBJECT_0 }
}

/// Held by the supervisor until success or an authoritative natural exit. A
/// failed stop never leaves a bare PID which could later designate a stranger.
pub struct ProcessTree { root: u32, known: Mutex<BTreeMap<u32, Arc<Process>>> }
impl ProcessTree {
    pub fn open(pid: u32) -> Result<Option<Arc<Self>>, String> {
        if pid == 0 { return Err("Refusing to retain PID 0".into()); }
        Ok(Process::open(pid)?.map(|root| Arc::new(Self { root: pid, known: Mutex::new(BTreeMap::from([(pid, root)])) })))
    }

    pub fn root_pid(&self) -> u32 { self.root }

    fn snapshot(&self) -> Result<Vec<Arc<Process>>, String> {
        let entries = process_parents()?;
        let mut known = self.known.lock().unwrap();
        let mut candidates: BTreeMap<u32, Arc<Process>> = BTreeMap::new();
        // First discover candidates from one static table, pinning each opened
        // identity. A PID may have been reused between that table and open().
        loop {
            let mut added = false;
            for &(pid, parent) in &entries {
                if known.contains_key(&pid) || candidates.contains_key(&pid) { continue; }
                let Some(parent) = known.get(&parent).or_else(|| candidates.get(&parent)) else { continue };
                if let Some(process) = Process::open(pid)? {
                    if process.born >= parent.born { candidates.insert(pid, process); added = true; }
                }
            }
            if !added { break; }
        }
        // With the candidate handles pinned, a second bounded table must agree
        // on actual parentage. Creation order alone does not exclude PID reuse.
        let current: BTreeMap<_, _> = process_parents()?.into_iter().collect();
        adopt_verified(&mut known, candidates, &entries, &current);
        Ok(known.values().cloned().collect())
    }

    /// One system command per explicit stop request. On a subsequent explicit
    /// request, retained live descendants remain addressable even if root died.
    pub async fn stop(self: &Arc<Self>) -> Result<Option<String>, String> {
        let before = self.snapshot()?;
        let live: Vec<_> = before.iter().filter(|process| process.alive()).collect();
        if live.is_empty() { return Ok(None); }
        let system = std::env::var_os("SystemRoot").or_else(|| std::env::var_os("WINDIR"))
            .filter(|root| !root.is_empty()).ok_or("Unable to terminate process tree: SystemRoot is not set")?;
        let executable = PathBuf::from(system).join("System32/taskkill.exe");
        let mut command = tokio::process::Command::new(&executable);
        command.creation_flags(0x0800_0000);
        if live.iter().any(|process| process.pid == self.root) { command.args(["/PID", &self.root.to_string()]); }
        else { for process in live { command.args(["/PID", &process.pid.to_string()]); } }
        let output = command.args(["/T", "/F"]).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped())
            .output().await.map_err(|error| format!("Cannot run {}: {error}", executable.display()))?;
        let detail = format!("taskkill for PID {} (exit {}): {} {}", self.root,
            output.status.code().map_or_else(|| "none".into(), |code| code.to_string()),
            command_text(&output.stdout).trim(), command_text(&output.stderr).trim());
        // Include children created while the command enumerated. Hold every
        // observed identity across failure so a later request cannot retarget it.
        let observed = self.snapshot()?;
        let count = observed.len();
        let remaining = tokio::task::spawn_blocking(move || wait_observed(&observed)).await.map_err(|error| error.to_string())?;
        if !remaining.is_empty() { return Err(format!("{detail}\nObserved owned processes still running or unqueryable: {remaining:?}")); }
        Ok((!output.status.success()).then(|| format!("已确认观察到的 {count} 个进程全部退出；命令报告保留为警告。{detail}")))
    }
}

fn adopt_verified(known: &mut BTreeMap<u32, Arc<Process>>, mut candidates: BTreeMap<u32, Arc<Process>>,
    entries: &[(u32, u32)], current: &BTreeMap<u32, u32>) {
    loop {
        let accepted: Vec<_> = entries.iter().filter_map(|&(pid, parent)|
            (candidates.contains_key(&pid) && known.contains_key(&parent) && current.get(&pid) == Some(&parent)).then_some(pid)).collect();
        if accepted.is_empty() { break; }
        for pid in accepted { known.insert(pid, candidates.remove(&pid).unwrap()); }
    }
}

fn process_parents() -> Result<Vec<(u32, u32)>, String> {
        let handle = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
        if handle == INVALID_HANDLE_VALUE { return Err(format!("Cannot snapshot owned tree: {}", std::io::Error::last_os_error())); }
        let handle = unsafe { OwnedHandle::from_raw_handle(handle) };
        let mut entry: PROCESSENTRY32W = unsafe { std::mem::zeroed() };
        entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
        let mut entries = Vec::new();
        let mut found = unsafe { Process32FirstW(handle.as_raw_handle(), &mut entry) } != 0;
        while found {
            entries.push((entry.th32ProcessID, entry.th32ParentProcessID));
            found = unsafe { Process32NextW(handle.as_raw_handle(), &mut entry) } != 0;
        }
        if unsafe { GetLastError() } != ERROR_NO_MORE_FILES { return Err(format!("Incomplete process snapshot: {}", std::io::Error::last_os_error())); }
        Ok(entries)
}

fn wait_observed(processes: &[Arc<Process>]) -> Vec<u32> {
    // Preserve the existing five-second native stop wait, shared by all
    // groups (Windows limits WaitForMultipleObjects to 64 handles). Not polling.
    let began = Instant::now();
    for group in processes.chunks(64) {
        let handles: Vec<_> = group.iter().map(|process| process.handle.as_raw_handle()).collect();
        let remaining = 5_000_u32.saturating_sub(began.elapsed().as_millis().min(5_000) as u32);
        if (unsafe { WaitForMultipleObjects(handles.len() as u32, handles.as_ptr(), 1, remaining) }) != WAIT_OBJECT_0 { break; }
    }
    processes.iter().filter(|process| process.alive()).map(|process| process.pid).collect()
}

fn command_text(bytes: &[u8]) -> String {
    if let Ok(text) = std::str::from_utf8(bytes) { return text.to_owned(); }
    decode_code_page(bytes, CP_OEMCP)
}
fn decode_code_page(bytes: &[u8], code_page: u32) -> String {
    if bytes.is_empty() { return String::new(); }
    let length = bytes.len().min(i32::MAX as usize) as i32;
    let count = unsafe { MultiByteToWideChar(code_page, 0, bytes.as_ptr(), length, std::ptr::null_mut(), 0) };
    if count <= 0 { return format!("<system output decoding failed: {} bytes>", bytes.len()); }
    let mut wide = vec![0_u16; count as usize];
    unsafe { MultiByteToWideChar(code_page, 0, bytes.as_ptr(), length, wide.as_mut_ptr(), count) };
    String::from_utf16_lossy(&wide)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn oem_taskkill_failure_is_readable_without_replacement_characters() {
        assert_eq!(decode_code_page(&[0xb2,0xd9,0xd7,0xf7,0xb2,0xbb,0xca,0xdc,0xd6,0xa7,0xb3,0xd6], 936), "操作不受支持");
        assert_eq!(command_text(b"exit 128"), "exit 128");
    }

    #[tokio::test]
    async fn stale_child_record_cannot_adopt_an_opened_unrelated_replacement() {
        let node = crate::runner::find_node(None).unwrap();
        let mut root = tokio::process::Command::new(&node).args(["-e", "setInterval(()=>{},1000)"]).kill_on_drop(true).spawn().unwrap();
        let mut foreign = tokio::process::Command::new(node).args(["-e", "setInterval(()=>{},1000)"]).kill_on_drop(true).spawn().unwrap();
        let root_identity = Process::open(root.id().unwrap()).unwrap().unwrap();
        let replacement = Process::open(foreign.id().unwrap()).unwrap().unwrap();
        assert!(replacement.born >= root_identity.born, "the former creation-order-only gate would accept it");
        let root_pid = root_identity.pid;
        let foreign_pid = replacement.pid;
        let mut known = BTreeMap::from([(root_pid, root_identity)]);
        // Controlled replay: the first snapshot described an old child with
        // this PID, but the opened object now belongs to the test process.
        adopt_verified(&mut known, BTreeMap::from([(foreign_pid, replacement)]), &[(foreign_pid, root_pid)],
            &BTreeMap::from([(foreign_pid, std::process::id())]));
        assert!(!known.contains_key(&foreign_pid));
        let tree = Arc::new(ProcessTree { root: root_pid, known: Mutex::new(known) });
        let stopped = tree.stop().await;
        if stopped.is_err() { let _ = root.kill().await; }
        root.wait().await.unwrap();
        let untouched = Process::open(foreign_pid).unwrap().unwrap().alive();
        foreign.kill().await.unwrap();
        assert!(stopped.is_ok() && untouched, "an unrelated replacement must not enter any later Stop target set");
    }

    #[test]
    fn retained_identity_survives_the_parent_dropping_an_exited_child_handle() {
        let node = crate::runner::find_node(None).unwrap();
        let mut child = std::process::Command::new(node).args(["-e", "process.exit(0)"]).spawn().unwrap();
        let pid = child.id();
        let tree = ProcessTree::open(pid).unwrap().unwrap();
        let born = tree.known.lock().unwrap()[&pid].born;
        child.wait().unwrap(); drop(child);
        let identity = Process::open(pid).unwrap().unwrap();
        assert_eq!(identity.born, born);
        assert!(!identity.alive());
    }

    #[tokio::test]
    async fn a_dead_root_is_not_enough_when_an_observed_descendant_is_still_alive() {
        let node = crate::runner::find_node(None).unwrap();
        let mut root = tokio::process::Command::new(&node).args(["-e", "setInterval(()=>{},1000)"]).kill_on_drop(true).spawn().unwrap();
        let mut descendant = tokio::process::Command::new(node).args(["-e", "setInterval(()=>{},1000)"]).kill_on_drop(true).spawn().unwrap();
        let root_identity = Process::open(root.id().unwrap()).unwrap().unwrap();
        let descendant_identity = Process::open(descendant.id().unwrap()).unwrap().unwrap();
        root.kill().await.unwrap();
        let observed = vec![root_identity, descendant_identity];
        let retained = observed.clone();
        let remaining = tokio::task::spawn_blocking(move || wait_observed(&retained)).await.unwrap();
        let expected = descendant.id().unwrap();
        descendant.kill().await.unwrap();
        assert_eq!(remaining, [expected], "root exit alone must not certify the observed tree");
        assert!(wait_observed(&observed).is_empty());
    }
}
