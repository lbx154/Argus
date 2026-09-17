"""Portable, user-local runtimes for optional plugins; no shell or global setup.

This file is also included in standalone plugin wheels. Keep it independent of
Argus imports. Downloads always verify TLS and, where published, SHA-256.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import tarfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

MAMBA_VERSION = "2.9.0"
MAMBA_HASHES = {
    "linux-64": "8761c382127e6363bd9e0a2451aa3ef90d071a79133f736e2f759a3bf13040dd",
    "osx-64": "0426ecdc41636d369f57b8fe6acbf4385a69eca45b56d9ee7d3a840a9965d44f",
    "osx-arm64": "500f5074feb8d02c4296ef9921c3650ed2874171805a9fbb8fbb53896433646b",
    "win-64": "97a336f4ab794bd96a6a4da5e6ed63e75a1d31830414a182419b23d3b36f3fe0",
}


def platform_key(system=None, machine=None):
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    arch = "64" if machine in {"x86_64", "amd64", "x64"} else "arm64"
    key = {"windows": "win", "darwin": "osx", "linux": "linux"}.get(system, system) + "-" + arch
    if key not in MAMBA_HASHES or machine not in {"x86_64", "amd64", "x64", "arm64", "aarch64"}:
        raise ValueError(f"当前处理器平台 {system}/{machine} 尚无完整科学软件发行包。")
    return key


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def download(url, destination, *, checksum=None, auth=None, context=None, limit=512 * 1024**2):
    """Stream to a temporary file. Never forward licensed credentials elsewhere."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if checksum and destination.is_file() and sha256(destination) == checksum:
        return destination
    if not url.startswith("https://") or urlparse(url).username:
        raise ValueError("下载源必须使用 HTTPS，且不能在 URL 中包含凭据。")
    temp = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".part")
    try:
        with httpx.Client(verify=context or True, timeout=90) as client:
            current = url
            for _ in range(8):
                with client.stream("GET", current, auth=auth) as response:
                    if response.is_redirect:
                        redirect = str(response.url.join(response.headers["location"]))
                        if not redirect.startswith("https://") or (
                            auth and urlparse(redirect).netloc != urlparse(url).netloc
                        ):
                            raise ValueError("下载站点发生不安全重定向，未发送授权凭据。")
                        current = redirect
                        continue
                    if response.status_code in {401, 403}:
                        raise ValueError("下载授权未通过，请核对用户名和密码或重新向官方网站申请。")
                    response.raise_for_status()
                    size = 0
                    with temp.open("wb") as output:
                        for part in response.iter_bytes():
                            size += len(part)
                            if size > limit:
                                raise ValueError("下载文件超过大小限制。")
                            output.write(part)
                    break
            else:
                raise ValueError("下载重定向次数过多。")
        if checksum and sha256(temp) != checksum:
            raise ValueError("下载文件 SHA-256 校验失败；保留已有安装。")
        os.replace(temp, destination)
        return destination
    finally:
        temp.unlink(missing_ok=True)


def clean_env():
    """No model keys, CLI homes, Python injection, or user conda configuration."""
    keep = {
        "PATH",
        "HOME",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "LANG",
        "LC_ALL",
        "SYSTEMDRIVE",
        # platform.machine() on Windows reads these non-sensitive OS values.
        # Dropping them makes the installer incorrectly detect "windows/".
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_ARCHITEW6432",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
    }
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update(
        PYTHONUTF8="1",
        PYTHONNOUSERSITE="1",
        OMP_NUM_THREADS="4",
        OPENBLAS_NUM_THREADS="4",
        MKL_NUM_THREADS="4",
    )
    return env


def run(command, *, cwd=None, env=None, timeout=1800, input=None, on_start=None):
    """Bounded process tree; a timed out package installer must not keep writing."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        [str(v) for v in command],
        cwd=cwd,
        env=env or clean_env(),
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        start_new_session=os.name != "nt",
    )
    try:
        if on_start:
            on_start(process.pid)
        output, _ = process.communicate(input=input, timeout=timeout)
    except BaseException:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
        else:
            import signal

            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate()
        raise
    if process.returncode:
        # Credential payloads are only sent to our installer via stdin, never
        # embedded in argv. Callers with credentials suppress untrusted output.
        raise RuntimeError(
            f"{Path(str(command[0])).name} 执行失败 ({process.returncode})\n{output[-3500:]}"
        )
    return output


def micromamba(root):
    root = Path(root)
    key = platform_key()
    binary = root / "bootstrap" / ("micromamba.exe" if os.name == "nt" else "micromamba")
    marker = binary.with_suffix(".sha256")
    if binary.is_file() and marker.is_file() and marker.read_text() == sha256(binary):
        return binary
    archive = download(
        f"https://api.anaconda.org/download/conda-forge/micromamba/{MAMBA_VERSION}/{key}/micromamba-{MAMBA_VERSION}-0.tar.bz2",
        root / "cache" / f"micromamba-{key}.tar.bz2",
        checksum=MAMBA_HASHES[key],
    )
    with tarfile.open(archive) as package:
        name = "Library/bin/micromamba.exe" if os.name == "nt" else "bin/micromamba"
        member = package.getmember(name)
        if not member.isfile() or member.size > 50 * 1024**2:
            raise ValueError("Invalid portable runtime package")
        binary.parent.mkdir(parents=True, exist_ok=True)
        temp = binary.with_suffix(".tmp")
        with package.extractfile(member) as source, temp.open("wb") as target:
            shutil.copyfileobj(source, target)
        temp.chmod(0o700)
        os.replace(temp, binary)
    marker.write_text(sha256(binary))
    return binary


def conda_env(root, prefix, packages, *, timeout=2700):
    root, prefix = Path(root), Path(prefix)
    binary = micromamba(root)
    env = clean_env()
    env.update(MAMBA_ROOT_PREFIX=str(root / "mamba"), MAMBA_NO_BANNER="1")
    output = run(
        [
            binary,
            "--no-rc",
            "create",
            "--yes",
            "--prefix",
            prefix,
            "--override-channels",
            "--channel",
            "conda-forge",
            "--strict-channel-priority",
            "--repodata-ttl",
            "86400",
            *packages,
        ],
        env=env,
        timeout=timeout,
    )
    return output


def portable_python(root):
    root = Path(root)
    prefix = root / "python-3.11"
    python = prefix / ("python.exe" if os.name == "nt" else "bin/python")
    if python.is_file():
        try:
            run(
                [python, "-I", "-c", "import venv,sys; assert sys.version_info[:2] == (3,11)"],
                timeout=20,
            )
            return str(python)
        except (OSError, RuntimeError):
            pass
    conda_env(root, prefix, ["python=3.11", "pip"])
    return str(python)
