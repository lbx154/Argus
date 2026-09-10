"""User-local official PLATON Windows runtime, without running an installer.

The proprietary plugin remains untouched. Its supported ``configure`` action
selects this verified external installation. Binaries are downloaded from their
publishers on the user's machine, never redistributed inside Argus releases.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import struct
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from .plugin_runtime import clean_env, download, run

OFFICIAL_PAGE = "https://www.chem.gla.ac.uk/~louis/software/platon/"
PLATON_URL = OFFICIAL_PAGE + "platon.zip"
PLATON_SHA256 = "a8c845891dc1dca1cb98d691917355c1bdb1c380e947a91893966d163900063b"
TASKBAR_URL = OFFICIAL_PAGE + "pwt_setup.zip"
TASKBAR_SHA256 = "b60fa412f7b2e95287826c364a9aa7fff5cd962bc11fae42d91cc4e70cfab276"
DLL_SHA256 = "a5d3aec4cb1897543ad142634c0a4fd6285b5dd52d7c701249a0e8582a4b9de0"
EXTRACTOR_URL = "https://constexpr.org/innoextract/files/innoextract-1.9/innoextract-1.9-windows.zip"
# Publisher distribution independently checked against Scoop's SHA-512.
EXTRACTOR_SHA256 = "6989342c9b026a00a72a38f23b62a8e6a22cc5de69805cf47d68ac2fec993065"
LICENSE_NOTICE = (
    "PLATON 官方允许注明来源的学术、科学及非商业用途；商业用途需自行取得许可。"
    "本操作只从官方网站下载到插件私有目录，不安装系统运行库、不修改系统 PATH。"
)


def _member(archive: Path, basename: str, *, limit: int = 20 * 1024**2) -> bytes:
    """Read one regular file, never extract arbitrary ZIP paths or links."""
    with zipfile.ZipFile(archive) as package:
        matches = []
        for entry in package.infolist():
            path = PurePosixPath(entry.filename.replace("\\", "/"))
            if path.name.casefold() != basename.casefold():
                continue
            if (path.is_absolute() or ".." in path.parts or ":" in entry.filename
                    or entry.is_dir() or (entry.external_attr >> 16) & 0o170000 == 0o120000
                    or entry.file_size > limit):
                raise ValueError("官方软件压缩包包含不安全或过大的文件，未安装。")
            matches.append(entry)
        if len(matches) != 1:
            raise ValueError(f"官方软件包必须包含唯一的 {basename}，未安装。")
        return package.read(matches[0])


def _machine(data: bytes) -> int:
    if len(data) < 64 or data[:2] != b"MZ":
        raise ValueError("Windows 科学软件不是有效的 PE 文件。")
    offset = struct.unpack_from("<I", data, 60)[0]
    if offset + 6 > len(data) or data[offset:offset + 4] != b"PE\0\0":
        raise ValueError("Windows 科学软件 PE 头损坏。")
    return struct.unpack_from("<H", data, offset + 4)[0]


def _write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as output:
        output.write(data)


def launcher_path() -> Path:
    return Path(__file__).resolve().parents[1] / "_native" / "platon-headless.exe"


def probe(executable: Path, *, on_start=None) -> None:
    """Generate check.def in an empty temporary directory, never research data."""
    with tempfile.TemporaryDirectory(prefix="argus-platon-check-") as directory:
        run([executable, "-z2"], cwd=directory, env=clean_env(), timeout=45, on_start=on_start)
        if not (Path(directory) / "check.def").is_file():
            raise RuntimeError("PLATON 未能生成校验规则，已有安装保持不变。")


def prepare(resources_root: Path, *, accept_license: bool = False, progress=print, on_start=None) -> Path:
    if accept_license is not True:
        raise ValueError("请先确认 PLATON 使用许可。" + LICENSE_NOTICE)
    if os.name != "nt":
        raise ValueError("此官方运行环境准备操作仅适用于 Windows。")
    root = Path(resources_root).resolve()
    launcher = launcher_path()
    if not launcher.is_file():
        raise ValueError("缺少 Windows PLATON 适配器，请使用完整 Windows 预览包或先构建桌面原生工具。")
    launcher_data = launcher.read_bytes()
    if _machine(launcher_data) != 0x8664:
        raise ValueError("Windows PLATON 适配器架构不符。")
    launcher_hash = hashlib.sha256(launcher_data).hexdigest()
    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    progress("正在校验 PLATON 官方程序与 Windows 配套运行环境…")
    program_archive = download(PLATON_URL, cache / "platon-win-64", checksum=PLATON_SHA256)
    program = _member(program_archive, "platon.exe")
    if _machine(program) != 0x14C:
        raise ValueError("PLATON 官方程序架构已变化，需要更新兼容配方。")
    program_hash = hashlib.sha256(program).hexdigest()
    from .plugin_manager import read_json, write_json

    marker = root / "host-platon-runtime.json"
    try:
        current = Path(read_json(marker)["path"]).resolve()
        if ((root / "software") in current.parents and not current.is_symlink()
                and hashlib.sha256(current.read_bytes()).hexdigest() == launcher_hash
                and hashlib.sha256((current.parent / "platon.exe").read_bytes()).hexdigest() == program_hash
                and hashlib.sha256((current.parent / "salflibc.dll").read_bytes()).hexdigest() == DLL_SHA256):
            probe(current, on_start=on_start)
            progress("PLATON 官方运行环境已可用，保留当前安装。")
            return current
    except (OSError, ValueError, KeyError, RuntimeError):
        pass

    taskbar = download(TASKBAR_URL, cache / "platon-taskbar-2026.1.zip", checksum=TASKBAR_SHA256)
    extractor_archive = download(EXTRACTOR_URL, cache / "innoextract-1.9-windows.zip", checksum=EXTRACTOR_SHA256)
    with tempfile.TemporaryDirectory(prefix="platon-prepare-", dir=root) as temporary:
        work = Path(temporary)
        extractor = work / "innoextract.exe"
        setup = work / "setup.exe"
        _write_new(extractor, _member(extractor_archive, "innoextract.exe"))
        _write_new(setup, _member(taskbar, "setup.exe"))
        progress("正在提取官方 Salford 运行库（不执行系统安装程序）…")
        extracted = work / "extracted"
        # setup.exe is input DATA to the pinned extractor, never a command.
        # Inno 5 installers use platform-specific internal path spellings;
        # extract this checksum-pinned archive into our disposable directory,
        # then deploy only the independently pinned DLL below.
        run([extractor, "--extract", "--output-dir", extracted, setup],
            timeout=90, on_start=on_start)
        libraries = list(extracted.rglob("salflibc.dll"))
        if len(libraries) != 1 or libraries[0].is_symlink():
            raise ValueError("官方配套安装包未提供唯一的 Salford 运行库。")
        library = libraries[0].read_bytes()
        if hashlib.sha256(library).hexdigest() != DLL_SHA256 or _machine(library) != 0x14C:
            raise ValueError("Salford 运行库校验或架构不符，未部署。")
        candidate = root / "software" / ("platon-windows-" + uuid.uuid4().hex[:12])
        candidate.mkdir(parents=True)
        executable = candidate / "platon-headless.exe"
        try:
            _write_new(executable, launcher_data)
            _write_new(candidate / "platon.exe", program)
            _write_new(candidate / "salflibc.dll", library)
            _write_new(candidate / "SOURCE-AND-LICENSE.txt", (
                LICENSE_NOTICE + "\n" + OFFICIAL_PAGE + "\n"
                "PLATON executable: " + PLATON_SHA256 + "\n"
                "Official Windows Taskbar archive: " + TASKBAR_SHA256 + "\n"
                "No proprietary plugin source or wheel has been modified.\n"
            ).encode("utf-8"))
            progress("正在实际运行 PLATON，验证 Windows 运行库和校验规则…")
            probe(executable, on_start=on_start)
        except BaseException:
            # Only our unpublished, newly-created candidate. Existing software
            # and the plugin's active registry are never deleted or replaced.
            shutil.rmtree(candidate, ignore_errors=True)
            raise
    write_json(marker, {"path": str(executable), "program_sha256": program_hash,
                        "runtime_sha256": DLL_SHA256, "launcher_sha256": launcher_hash,
                        "source": OFFICIAL_PAGE,
                        "license_accepted": True})
    progress("PLATON 官方 Windows 运行环境验证通过。")
    return executable
