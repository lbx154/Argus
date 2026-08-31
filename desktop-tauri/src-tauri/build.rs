use std::{env, fs, path::PathBuf};

/// GNU Windows builds link WebView2Loader dynamically, while MSVC builds link
/// Microsoft's static loader. Ship the official architecture-matched loader in
/// both cases so an NSIS package never relies on a DLL that happened to exist
/// in the build machine's PATH.
fn stage_webview2_loader() {
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").expect("manifest dir"));
    let target_arch = match env::var("CARGO_CFG_TARGET_ARCH").as_deref() {
        Ok("x86_64") => "x64",
        Ok("x86") => "x86",
        Ok("aarch64") => "arm64",
        Ok(other) => panic!("unsupported WebView2 loader architecture: {other}"),
        Err(error) => panic!("missing Cargo target architecture: {error}"),
    };
    let destination = manifest_dir
        .parent()
        .expect("src-tauri has desktop-tauri parent")
        .join("resources")
        .join("WebView2Loader.dll");
    let source = find_webview2_loader(target_arch).unwrap_or_else(|| {
        panic!(
            "WebView2Loader.dll for {target_arch} was not found in the Cargo registry; run cargo fetch before packaging"
        )
    });
    fs::create_dir_all(destination.parent().expect("loader parent"))
        .expect("create WebView2 loader resource directory");
    let should_copy = fs::read(&destination).ok().as_deref() != fs::read(&source).ok().as_deref();
    if should_copy {
        fs::copy(&source, &destination).expect("stage WebView2Loader.dll");
    }
    println!("cargo:rerun-if-env-changed=CARGO_HOME");
    println!("cargo:rerun-if-changed={}", source.display());
    println!("cargo:rerun-if-changed={}", destination.display());
}

fn find_webview2_loader(target_arch: &str) -> Option<PathBuf> {
    let cargo_home = env::var_os("CARGO_HOME")
        .map(PathBuf::from)
        .or_else(|| env::var_os("USERPROFILE").map(|home| PathBuf::from(home).join(".cargo")))?;
    let registry = cargo_home.join("registry").join("src");
    let registries = fs::read_dir(registry).ok()?;
    for registry in registries.flatten() {
        let packages = match fs::read_dir(registry.path()) {
            Ok(packages) => packages,
            Err(_) => continue,
        };
        for package in packages.flatten() {
            let name = package.file_name();
            if !name.to_string_lossy().starts_with("webview2-com-sys-") {
                continue;
            }
            let candidate = package.path().join(target_arch).join("WebView2Loader.dll");
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

fn main() {
    stage_webview2_loader();
    tauri_build::build()
}
