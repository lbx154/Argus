"""Install the official standalone Copilot CLI into Argus's private runtime."""
from __future__ import annotations

import http.client
import os
import platform
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

import certifi

VERSION = "1.0.83"
DOWNLOAD_HELP = "请检查网络，或开启代理（梯子）的系统代理 / TUN 模式后重试。"
# GitHub's release asset sizes, in bytes, for this pinned CLI release.
ASSETS = {
    ("Darwin", "arm64"): ("copilot-darwin-arm64.tar.gz", 83738610),
    ("Darwin", "x86_64"): ("copilot-darwin-x64.tar.gz", 96392970),
    ("Windows", "x86_64"): ("copilot-win32-x64.zip", 85369705),
    ("Windows", "arm64"): ("copilot-win32-arm64.zip", 87106506),
    ("Linux", "x86_64"): ("copilot-linux-x64.tar.gz", 91269605),
}


def asset_for(system: str, machine: str) -> tuple[str, int]:
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


def _download_archive(request, archive: Path, expected: int, progress=None):
    context = ssl.create_default_context(cafile=certifi.where())
    received = 0
    last_report = 0.0
    try:
        with urllib.request.urlopen(request, timeout=90, context=context) as response, archive.open("wb") as target:
            try:
                total = int(response.headers.get("Content-Length", "0")) or None
                if total is not None and total < 0:
                    total = None
            except (TypeError, ValueError):
                total = None
            if progress:
                progress(0, total)
            read = getattr(response, "read1", response.read)
            while chunk := read(64 * 1024):
                target.write(chunk)
                received += len(chunk)
                now = time.monotonic()
                if progress and now - last_report >= 0.25:
                    progress(received, total)
                    last_report = now
            if total is not None and received != total:
                raise ValueError("Copilot 下载中断，安装包不完整。" + DOWNLOAD_HELP)
            if progress:
                progress(received, total)
    except (TimeoutError, urllib.error.URLError, ConnectionError, ssl.SSLError, http.client.HTTPException):
        raise ValueError("Copilot 下载中断或等待网络响应超时；原设置未更改。" + DOWNLOAD_HELP) from None
    if received != expected:
        raise ValueError("Copilot 下载校验失败，请重试。")


def install_native_copilot(
    *, progress=print,
    download_progress: Callable[[int, int | None], None] | None = None,
) -> str:
    from ..core.paths import global_root

    report = progress or (lambda _message: None)
    system = platform.system()
    name, expected = asset_for(system, platform.machine())
    executable_name = "copilot.exe" if system == "Windows" else "copilot"
    root = global_root() / "runtime" / "copilot" / VERSION
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    executable = root / executable_name
    if executable.is_file():
        report("正在检查已安装的 Copilot…")
        verify_cli(executable)
        return str(executable)
    # A failed or interrupted download leaves no partly installed executable.
    with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as temporary:
        archive = Path(temporary) / name
        request = urllib.request.Request(
            f"https://github.com/github/copilot-cli/releases/download/v{VERSION}/{name}",
            headers={"User-Agent": "Argus/0.1.1"},
        )
        report("正在下载 Copilot…")
        _download_archive(request, archive, expected, download_progress)
        report("下载完成，正在安装已校验的 Copilot…")
        staged = Path(temporary) / executable_name
        extract_binary(archive, staged, executable_name)
        # Publish the size-verified bytes before launching: on Windows the
        # CLI's background processes can keep its executable locked after help
        # exits, preventing a rename or temporary-directory cleanup.
        os.replace(staged, executable)
    report("正在检查 Copilot 运行环境…")
    verify_cli(executable)
    return str(executable)
