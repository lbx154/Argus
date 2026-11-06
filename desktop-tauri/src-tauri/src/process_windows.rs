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
pub struct ProcessTree {
    root: u32,
    known: Mutex<BTreeMap<u32, Arc<Process>>>,
    // An unidentifiable failure cannot be erased by a later empty snapshot.
    unresolved: Mutex<Option<String>>,
}
impl ProcessTree {
    pub fn open(pid: u32) -> Result<Option<Arc<Self>>, String> {
        if pid == 0 { return Err("Refusing to retain PID 0".into()); }
        Ok(Process::open(pid)?.map(|root| Arc::new(Self { root: pid, known: Mutex::new(BTreeMap::from([(pid, root)])), unresolved: Mutex::new(None) })))
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
        if let Some(error) = self.unresolved.lock().unwrap().clone() { return Err(error); }
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
            taskkill_text(&output.stdout).trim(), taskkill_text(&output.stderr).trim());
        let detail = if output.status.success() { detail } else {
            format!("{detail}\nraw stdout hex: {}\nraw stderr hex: {}", hex(&output.stdout), hex(&output.stderr))
        };
        // Include children created while the command enumerated. Hold every
        // observed identity across failure so a later request cannot retarget it.
        let observed = match self.snapshot() {
            Ok(observed) => observed,
            Err(error) => {
                let error = format!("{detail}\nCannot verify post-stop identities: {error}");
                *self.unresolved.lock().unwrap() = Some(error.clone());
                return Err(error);
            }
        };
        let count = observed.len();
        let known_ids: Vec<_> = observed.iter().map(|process| process.pid).collect();
        let remaining = tokio::task::spawn_blocking(move || wait_observed(&observed)).await.map_err(|error| error.to_string())?;
        if !remaining.is_empty() { return Err(format!("{detail}\nObserved owned processes still running or unqueryable: {remaining:?}")); }
        if !output.status.success() && !taskkill_failures_covered(&output.stdout, &output.stderr, &known_ids) {
            let error = format!("{detail}\n无法核实全部 taskkill 失败目标的身份；未将非零命令降为警告，请人工检查。仍保留已有身份，不自动清除此失败。");
            *self.unresolved.lock().unwrap() = Some(error.clone());
            return Err(error);
        }
        Ok((!output.status.success()).then(|| format!("已确认观察到的 {count} 个进程及命令报告的失败目标全部退出；命令原始结果保留为警告。{detail}")))
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

fn hex(bytes: &[u8]) -> String { bytes.iter().map(|byte| format!("{byte:02x}")).collect() }

// Parse only the fixed taskkill error-record envelope, directly from bytes.
// This does not infer success from a reason such as "not supported". Unknown
// layouts/IDs fail closed; actual success still requires native exit events.
fn taskkill_failures_covered(stdout: &[u8], stderr: &[u8], known: &[u32]) -> bool {
    let lines = |bytes: &[u8]| -> Vec<Vec<u8>> { bytes.split(|byte| *byte == b'\n').map(|line| {
        let first = line.iter().position(|byte| !byte.is_ascii_whitespace()).unwrap_or(line.len());
        let last = line.iter().rposition(|byte| !byte.is_ascii_whitespace()).map_or(first, |index| index + 1);
        line[first..last].to_vec()
    }).filter(|line| !line.is_empty()).collect() };
    let prefix = |line: &[u8], english: &[u8], utf8: &str, cp936: &[u8]| {
        line.get(..english.len()).is_some_and(|head| head.eq_ignore_ascii_case(english)) || line.starts_with(utf8.as_bytes()) || line.starts_with(cp936)
    };
    if lines(stdout).iter().any(|line| !prefix(line, b"SUCCESS:", "成功:", b"\xb3\xc9\xb9\xa6:")) { return false; }
    let records = lines(stderr);
    if records.is_empty() { return false; }
    let expression = regex::Regex::new(r#"(?i)PID\s+(\d+)|"(\d+)""#).unwrap();
    let mut index = 0;
    while index < records.len() {
        if !prefix(&records[index], b"ERROR:", "错误:", b"\xb4\xed\xce\xf3:") { return false; }
        let start = index; index += 1;
        let reason = index < records.len() && prefix(&records[index], b"Reason:", "原因:", b"\xd4\xad\xd2\xf2:");
        if reason { index += 1; }
        let ascii: String = records[start..index].iter().flat_map(|line| line.iter().chain(std::iter::once(&b' '))).map(|byte| if byte.is_ascii() { char::from(*byte) } else { ' ' }).collect();
        let captures: Vec<_> = expression.captures_iter(&ascii).collect();
        let pid_fields = captures.iter().filter(|matched| matched.get(1).is_some()).count();
        if captures.is_empty() || ascii.to_ascii_uppercase().matches("PID").count() != pid_fields { return false; }
        // The observed one-line "process \"123\" not found" form has no
        // Reason field. Other one-line/unrecognised envelopes fail closed.
        if !reason && (pid_fields != 0 || captures.len() != 1) { return false; }
        if captures.iter().any(|matched| matched.get(1).or_else(|| matched.get(2)).unwrap().as_str().parse::<u32>().ok().map_or(true, |pid| !known.contains(&pid))) { return false; }
    }
    true
}

// Only taskkill uses this diagnostic decoder. Raw redirected bytes are kept
// above. The tested host's ACP/OEM are both936; other tool formats are not
// assumed to use OEM, and decoding never decides termination success.
fn taskkill_text(bytes: &[u8]) -> String {
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
    fn nonzero_taskkill_requires_every_reported_failure_identity_to_be_covered() {
        let stdout = b"SUCCESS: The process with PID 10 has been terminated.\r\n";
        let stderr = b"ERROR: Unable to terminate PID 11 (child of PID 10).\r\nReason: Access is denied.\r\n";
        assert!(taskkill_failures_covered(stdout, stderr, &[10, 11]));
        assert!(!taskkill_failures_covered(stdout, stderr, &[10]), "an uncaptured failed PID must block restart");
        assert!(!taskkill_failures_covered(stdout, b"Unknown enumeration failure", &[10, 11]));
        // Trimmed PID from the actual redirected taskkill exit128 receipt.
        let not_found = b"\xb4\xed\xce\xf3: \xc3\xbb\xd3\xd0\xd5\xd2\xb5\xbd\xbd\xf8\xb3\xcc \"42\"\xa1\xa3\r\n";
        assert!(taskkill_failures_covered(b"", not_found, &[42]));
        assert!(!taskkill_failures_covered(b"", not_found, &[10, 11]));
        assert!(!taskkill_failures_covered(b"Unknown stdout error", stderr, &[10, 11]));
        assert!(!taskkill_failures_covered(stdout, b"ERROR: PID 99999999999999999999 failed.\nReason: failed\n", &[10, 11]));
        assert_eq!(hex(&[0x80, 0x00, 0xff]), "8000ff", "original bytes remain recoverable regardless of decoding");
    }

    #[test]
    fn oem_taskkill_failure_is_readable_without_replacement_characters() {
        assert_eq!(decode_code_page(&[0xb2,0xd9,0xd7,0xf7,0xb2,0xbb,0xca,0xdc,0xd6,0xa7,0xb3,0xd6], 936), "操作不受支持");
        assert_eq!(taskkill_text(b"exit 128"), "exit 128");
    }

    #[tokio::test]
    async fn an_unidentified_failure_is_not_cleared_by_a_later_empty_live_set() {
        let mut child = std::process::Command::new(crate::runner::find_node(None).unwrap()).args(["-e", "process.exit(0)"]).spawn().unwrap();
        let tree = ProcessTree::open(child.id()).unwrap().unwrap();
        child.wait().unwrap();
        *tree.unresolved.lock().unwrap() = Some("unidentified failure target; manual check required".into());
        assert_eq!(tree.stop().await.unwrap_err(), "unidentified failure target; manual check required");
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
        let tree = Arc::new(ProcessTree { root: root_pid, known: Mutex::new(known), unresolved: Mutex::new(None) });
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
