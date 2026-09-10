//! First-party command adapter for the unmodified official Windows PLATON.
//! The publisher's PWT taskbar passes +00 to close the completion dialog.
//! Preserve native argument boundaries and the child's actual exit status.
use std::{env, ffi::OsString, path::PathBuf, process::{Command, ExitCode}};
#[cfg(windows)]
use std::os::windows::process::CommandExt;

fn arguments(input: impl Iterator<Item = OsString>) -> Vec<OsString> {
    input.chain(std::iter::once(OsString::from("+00"))).collect()
}

fn run() -> std::io::Result<i32> {
    let executable = env::current_exe()?;
    let program: PathBuf = executable.parent().ok_or_else(||
        std::io::Error::other("Cannot locate the PLATON installation"))?.join("platon.exe");
    if !program.is_file() || program == executable {
        return Err(std::io::Error::new(std::io::ErrorKind::NotFound,
            "The official platon.exe must remain beside platon-headless.exe"));
    }
    let mut command = Command::new(program);
    command.args(arguments(env::args_os().skip(1)));
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW; never a shell.
    Ok(command.status()?.code().unwrap_or(1))
}

fn main() -> ExitCode {
    match run() {
        Ok(code) => std::process::exit(code),
        Err(error) => {
            eprintln!("PLATON launch failed: {error}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preserves_spaces_unicode_and_shell_metacharacters_as_one_argument() {
        let input = vec![OsString::from("-u"), OsString::from("D:/晶体数据/a & b.cif")];
        let output = arguments(input.clone().into_iter());
        assert_eq!(&output[..2], &input);
        assert_eq!(output[2], "+00");
    }
    #[test]
    fn closes_the_official_completion_dialog_without_changing_probe_options() {
        assert_eq!(arguments([OsString::from("-z2")].into_iter()),
                   vec![OsString::from("-z2"), OsString::from("+00")]);
    }
}
