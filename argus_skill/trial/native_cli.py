"""Install the official standalone Copilot CLI into Argus's private runtime."""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import certifi

VERSION = "1.0.83"
# GitHub's release asset SHA-256 values for this pinned CLI release.
ASSETS = {
    ("Darwin", "arm64"): ("copilot-darwin-arm64.tar.gz", "80a5ded6f1db484b4661af676ea914605ecfbcaf49f6b4bed81e6df16cbd56bd"),
    ("Darwin", "x86_64"): ("copilot-darwin-x64.tar.gz", "7e4f7236b0cd5ee474e6ab6d35ea67b8c33d5ec6483498e0fdd0218f458b2d53"),
    ("Windows", "x86_64"): ("copilot-win32-x64.zip", "0e07221a275fdf7e61619c53566e3a421fd646d74d8e9ca491dbbff221f22945"),
    ("Windows", "arm64"): ("copilot-win32-arm64.zip", "63f35c0ce1a5fdcc6f3e584890d689b1ede8f930933394aaf7b5e139b53d2cc1"),
    ("Linux", "x86_64"): ("copilot-linux-x64.tar.gz", "ffbe1c429664b8a05efed67ecdb467123e40fcaa3c6c14ef9a98ba74da4687b7"),
}


def asset_for(system: str, machine: str) -> tuple[str, str]:
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine.lower(), machine.lower())
    try:
        return ASSETS[system, arch]
    except KeyError:
        raise ValueError("此系统暂不支持自动安装 Copilot，请使用 Mac 或 Windows 的 64 位客户端。") from None


def extract_binary(archive: Path, destination: Path, name: str):
    """Extract exactly the executable; never unpack arbitrary archive paths."""
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as package, package.open(name) as source, destination.open("wb") as target:
            shutil.copyfileobj(source, target)
    else:
        with tarfile.open(archive) as package:
            entry = package.getmember(name)
            if not entry.isfile():
                raise ValueError("Copilot 安装包中的程序格式无效。")
            with package.extractfile(entry) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    destination.chmod(0o700)


def verify_cli(executable: Path):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        [str(executable), "help", "providers"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=45, **options,
    )
    if result.returncode or "COPILOT_PROVIDER_WIRE_MODEL" not in result.stdout:
        raise ValueError("Copilot 未能启动，请重试或检查系统是否阻止了此程序。")


def install_native_copilot() -> str:
    from ..core.paths import global_root

    system = platform.system()
    name, expected = asset_for(system, platform.machine())
    executable_name = "copilot.exe" if system == "Windows" else "copilot"
    root = global_root() / "runtime" / "copilot" / VERSION
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    executable = root / executable_name
    if executable.is_file():
        verify_cli(executable)
        return str(executable)
    # A failed or interrupted download leaves no partly installed executable.
    with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as temporary:
        archive = Path(temporary) / name
        request = urllib.request.Request(
            f"https://github.com/github/copilot-cli/releases/download/v{VERSION}/{name}",
            headers={"User-Agent": "Argus/0.1.1"},
        )
        digest = hashlib.sha256()
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, timeout=90, context=context) as response, archive.open("wb") as target:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                target.write(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("Copilot 下载校验失败，请重试。")
        staged = Path(temporary) / executable_name
        extract_binary(archive, staged, executable_name)
        # Publish the checksum-verified bytes before launching: on Windows the
        # CLI's background processes can keep its executable locked after help
        # exits, preventing a rename or temporary-directory cleanup.
        os.replace(staged, executable)
    verify_cli(executable)
    return str(executable)
