//! PLATON's published FINDEXE/PLA429 code stores SHELXL and SHELXT paths
//! in CHARACTER(len=80), even though scientific installations can be much deeper.
//! Use its official SHLEXE/SHTEXE selectors with short first-party forwarders.
//! Keep PATH, the original executables, working directory and tool arguments intact.
use std::{
    env,
    ffi::{OsStr, OsString},
    fs::{self, OpenOptions},
    io,
    path::{Path, PathBuf},
    process::Command,
    sync::atomic::{AtomicU64, Ordering},
    time::{SystemTime, UNIX_EPOCH},
};

struct Tool {
    selector: &'static str,
    managed: &'static str,
    forward: &'static str,
    alias: &'static str,
    names: &'static [&'static str],
}

const TOOLS: [Tool; 2] = [
    Tool {
        selector: "SHLEXE",
        managed: "CRYSTALPILOT_SHELXL",
        forward: "ARGUS_PLATON_FORWARD_SHELXL",
        alias: "axl.exe",
        names: &["shelxl.exe", "xl.exe"],
    },
    Tool {
        selector: "SHTEXE",
        managed: "CRYSTALPILOT_SHELXT",
        forward: "ARGUS_PLATON_FORWARD_SHELXT",
        alias: "axt.exe",
        names: &["shelxt.exe", "xt.exe"],
    },
];

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}

fn absolute_file(path: PathBuf) -> io::Result<PathBuf> {
    let path = if path.is_absolute() {
        path
    } else {
        env::current_dir()?.join(path)
    };
    if !path.is_file() {
        return Err(io::Error::new(io::ErrorKind::NotFound,
            "The configured SHELX executable is unavailable; existing installations were not changed"));
    }
    Ok(path)
}

fn selected_tool(
    tool: &Tool,
    lookup: &impl Fn(&str) -> Option<OsString>,
) -> io::Result<Option<PathBuf>> {
    let explicit = lookup(tool.selector).filter(|value| !value.is_empty());
    if let Some(value) = explicit.or_else(|| lookup(tool.managed).filter(|value| !value.is_empty()))
    {
        return absolute_file(PathBuf::from(value)).map(Some);
    }
    if let Some(path) = lookup("PATH") {
        for directory in env::split_paths(&path) {
            for name in tool.names {
                let candidate = directory.join(name);
                if candidate.is_file() {
                    return absolute_file(candidate).map(Some);
                }
            }
        }
    }
    Ok(None) // SHELX is optional; do not fabricate an installed tool.
}

fn legacy_safe(path: &Path) -> bool {
    path.is_absolute()
        && path.to_str().is_some_and(|text| {
            text.len() < 80
                && text.is_ascii()
                && text
                    .chars()
                    .all(|ch| ch.is_ascii_alphanumeric() || r"\/:._-~".contains(ch))
                && !text.starts_with(r"\\?\")
        })
}

#[cfg(windows)]
fn short_path(path: &Path) -> Option<PathBuf> {
    use std::os::windows::ffi::{OsStrExt, OsStringExt};
    #[link(name = "kernel32")]
    extern "system" {
        fn GetShortPathNameW(long: *const u16, short: *mut u16, size: u32) -> u32;
    }
    let input: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let size = unsafe { GetShortPathNameW(input.as_ptr(), std::ptr::null_mut(), 0) };
    if size == 0 {
        return None;
    }
    let mut output = vec![0u16; size as usize];
    let count = unsafe { GetShortPathNameW(input.as_ptr(), output.as_mut_ptr(), size) };
    if count == 0 || count >= size {
        return None;
    }
    Some(PathBuf::from(OsString::from_wide(
        &output[..count as usize],
    )))
}

#[cfg(not(windows))]
fn short_path(_path: &Path) -> Option<PathBuf> {
    None
}

/// Own only newly-created helper files. Never recursively remove anything and
/// never unlink the licensed programs or another invocation's temporary files.
pub(super) struct Aliases {
    directory: PathBuf,
    files: Vec<PathBuf>,
}

impl Aliases {
    fn new() -> io::Result<Self> {
        static SERIAL: AtomicU64 = AtomicU64::new(0);
        let temporary = env::temp_dir();
        let base = short_path(&temporary).unwrap_or(temporary);
        for _ in 0..32 {
            let clock = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos() as u64;
            let nonce = (clock
                ^ ((std::process::id() as u64) << 32)
                ^ SERIAL.fetch_add(1, Ordering::Relaxed))
                & 0xffffffffffff;
            let directory = base.join(format!("ap-{nonce:012x}"));
            if !legacy_safe(&directory.join(TOOLS[0].alias)) {
                return Err(invalid("PLATON's SHELX bridge needs an ASCII temporary path shorter than 80 characters; no PATH entries or scientific tools were removed"));
            }
            match fs::create_dir(&directory) {
                Ok(()) => {
                    return Ok(Self {
                        directory,
                        files: Vec::new(),
                    })
                }
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error),
            }
        }
        Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "Could not allocate a private PLATON bridge directory",
        ))
    }

    fn add(&mut self, executable: &Path, name: &str) -> io::Result<PathBuf> {
        let destination = self.directory.join(name);
        match fs::hard_link(executable, &destination) {
            Ok(()) => self.files.push(destination.clone()),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => return Err(error),
            Err(_) => {
                // Cross-volume/FAT installations may not support hard links.
                // Copy only our own first-party adapter with exclusive creation.
                let mut source = fs::File::open(executable)?;
                let mut output = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&destination)?;
                self.files.push(destination.clone());
                io::copy(&mut source, &mut output)?;
            }
        }
        Ok(destination)
    }
}

impl Drop for Aliases {
    fn drop(&mut self) {
        for file in &self.files {
            let _ = fs::remove_file(file);
        }
        let _ = fs::remove_dir(&self.directory);
    }
}

pub(super) fn configure(command: &mut Command, executable: &Path) -> io::Result<Option<Aliases>> {
    configure_with(command, executable, &|name| env::var_os(name))
}

fn configure_with(
    command: &mut Command,
    executable: &Path,
    lookup: &impl Fn(&str) -> Option<OsString>,
) -> io::Result<Option<Aliases>> {
    let mut aliases: Option<Aliases> = None;
    for tool in &TOOLS {
        let Some(target) = selected_tool(tool, lookup)? else {
            continue;
        };
        let direct = if legacy_safe(&target) {
            Some(target.clone())
        } else {
            short_path(&target).filter(|path| legacy_safe(path))
        };
        if let Some(path) = direct {
            command.env(tool.selector, path);
        } else {
            if aliases.is_none() {
                aliases = Some(Aliases::new()?);
            }
            let helper = aliases.as_mut().unwrap().add(executable, tool.alias)?;
            command.env(tool.selector, helper).env(tool.forward, target);
        }
    }
    Ok(aliases)
}

/// Invoked by PLATON via SHLEXE/SHTEXE. Forward exactly once, without +00,
/// shell parsing or changing cwd, so the real SHELX program resolves its own DLLs.
pub(super) fn forwarded_command(
    executable: &Path,
    arguments: impl Iterator<Item = OsString>,
) -> io::Result<Option<Command>> {
    let Some(name) = executable.file_name().and_then(OsStr::to_str) else {
        return Ok(None);
    };
    let Some(tool) = TOOLS
        .iter()
        .find(|tool| name.eq_ignore_ascii_case(tool.alias))
    else {
        return Ok(None);
    };
    let target =
        env::var_os(tool.forward).ok_or_else(|| invalid("Missing private SHELX bridge target"))?;
    let target = absolute_file(PathBuf::from(target))?;
    if fs::canonicalize(&target)? == fs::canonicalize(executable)? {
        return Err(invalid("A SHELX bridge cannot invoke itself"));
    }
    let mut command = Command::new(target);
    command.args(arguments);
    Ok(Some(command))
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[test]
    fn legacy_paths_are_bounded_ascii_absolute_and_shell_safe() {
        assert!(legacy_safe(Path::new(r"C:\tools\shelxl.exe")));
        for path in [
            "shelxl.exe",
            r"C:\science tools\shelxl.exe",
            r"C:\晶体\shelxt.exe",
            r"C:\a&b\shelxl.exe",
            r"C:\%PATH%\shelxl.exe",
            r"\\?\C:\tools\shelxl.exe",
        ] {
            assert!(!legacy_safe(Path::new(path)), "{path}");
        }
        assert!(!legacy_safe(&PathBuf::from(format!(
            "C:\\{}",
            "x".repeat(77)
        ))));
    }

    #[test]
    fn no_shelx_means_no_fake_tools_or_path_changes() {
        let mut command = Command::new("platon.exe");
        assert!(
            configure_with(&mut command, &env::current_exe().unwrap(), &|_| None)
                .unwrap()
                .is_none()
        );
        assert_eq!(command.get_envs().count(), 0);
    }

    #[test]
    fn explicit_missing_tool_is_not_silently_replaced_by_path_search() {
        let files = Aliases::new().unwrap();
        let missing = files.directory.join("not-installed.exe");
        let lookup = |name: &str| {
            if name == "SHLEXE" {
                Some(missing.clone().into_os_string())
            } else {
                None
            }
        };
        assert!(selected_tool(&TOOLS[0], &lookup).is_err());
    }

    #[test]
    fn discovery_honors_explicit_then_managed_then_path_and_alias_names() {
        let mut files = Aliases::new().unwrap();
        let executable = env::current_exe().unwrap();
        let primary = files.add(&executable, "shelxl.exe").unwrap();
        let alternate = files.add(&executable, "xl.exe").unwrap();
        let mut values = HashMap::from([
            ("SHLEXE", alternate.clone().into_os_string()),
            ("CRYSTALPILOT_SHELXL", primary.clone().into_os_string()),
            ("PATH", files.directory.clone().into_os_string()),
        ]);
        assert_eq!(
            selected_tool(&TOOLS[0], &|key| values.get(key).cloned()).unwrap(),
            Some(alternate)
        );
        values.remove("SHLEXE");
        assert_eq!(
            selected_tool(&TOOLS[0], &|key| values.get(key).cloned()).unwrap(),
            Some(primary.clone())
        );
        values.remove("CRYSTALPILOT_SHELXL");
        assert_eq!(
            selected_tool(&TOOLS[0], &|key| values.get(key).cloned()).unwrap(),
            Some(primary)
        );
    }

    #[test]
    fn short_selected_tools_do_not_change_path_or_need_forwarders() {
        let mut files = Aliases::new().unwrap();
        let target = files
            .add(&env::current_exe().unwrap(), "shelxt.exe")
            .unwrap();
        let lookup =
            |name: &str| (name == "CRYSTALPILOT_SHELXT").then(|| target.clone().into_os_string());
        let mut command = Command::new("platon.exe");
        assert!(
            configure_with(&mut command, &env::current_exe().unwrap(), &lookup)
                .unwrap()
                .is_none()
        );
        let environment: Vec<_> = command.get_envs().collect();
        assert_eq!(
            environment,
            vec![(OsStr::new("SHTEXE"), Some(target.as_os_str()))]
        );
    }

    #[test]
    fn helper_creation_is_exclusive_and_cleanup_never_walks_other_files() {
        let mut files = Aliases::new().unwrap();
        let directory = files.directory.clone();
        let helper = files.add(&env::current_exe().unwrap(), "axl.exe").unwrap();
        assert!(files.add(&env::current_exe().unwrap(), "axl.exe").is_err());
        let sentinel = directory.join("retain.txt");
        fs::write(&sentinel, "unrelated").unwrap();
        drop(files);
        assert!(!helper.exists());
        assert_eq!(fs::read_to_string(&sentinel).unwrap(), "unrelated");
        fs::remove_file(sentinel).unwrap(); // Only this test's own fixture.
        fs::remove_dir(directory).unwrap();
    }
}
