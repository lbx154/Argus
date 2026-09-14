"""Optional workbench installation, isolated packages and atomic activation.

The catalog is host-owned release metadata. The browser selects an id, never an
arbitrary URL, command or Python package. Research data is not an install payload.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path

import httpx
import portalocker

from .paths import global_root

API_VERSION = 1
UNSUPPORTED = "暂不支持，敬请期待。当前插件支持 Codex、Copilot 和 Pi。"
# A deployment lists the catalog ids that must simply be there, comma separated.
# The web server prepares them at startup and the interface shows them as
# provided by the service, with no install, disable or uninstall controls.
PREINSTALL_ENV = "ARGUS_PLUGINS_PREINSTALL"
HOST_MANAGED = "此插件由服务方提供并维护，无需停用或卸载。"
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_loaded: dict[tuple[str, str, str], object] = {}
_jobs: dict[tuple[str, str], threading.Thread] = {}
_lock = threading.RLock()
log = logging.getLogger(__name__)
_state_io_lock = threading.RLock()


class PluginError(ValueError):
    pass


class PluginUnavailableError(PluginError):
    """An explicitly selected plugin cannot execute; never reroute its work."""


def require_plugin(plugin_id, root=None):
    try:
        plugin = load_plugin(plugin_id, root)
    except Exception as exc:
        raise PluginUnavailableError(
            f"插件 {plugin_id} 加载失败，任务未执行；请检查插件安装和宿主目录。"
        ) from exc
    if plugin is None:
        raise PluginUnavailableError(
            f"插件 {plugin_id} 未安装、未启用或无法从宿主目录加载，任务未执行。"
            "请在插件中心检查；不会切换为 research 执行。"
        )
    return plugin


def session_plugin_name(life_dir):
    """Resolve a reserved session namespace, never classify an objective."""
    sid = Path(life_dir).name
    return next((name for name in catalog() if sid.startswith("s-" + name + "-")), None)


def require_session_plugin(life_dir, *, vertical=None, working_dir=None, check_binding=False):
    name = session_plugin_name(life_dir)
    if name is None:
        return None  # Native sessions may add tools without changing vertical.
    plugin = require_plugin(name)
    if vertical is not None and vertical != name:
        raise PluginUnavailableError(
            f"插件 {name} 工作台记录曾路由到其他流程，已阻止继续执行。"
            "原记录保持不变，请新建会话验收；不会自动改写旧任务。"
        )
    if check_binding and not plugin.owns_workdir(working_dir):
        raise PluginUnavailableError(f"插件 {name} 的任务目录绑定缺失，任务未执行；请新建工作台会话。")
    return plugin


def host_root(root=None):
    return Path(root or os.environ.get("ARGUS_WORKBENCH_HOST_ROOT") or global_root()).resolve()


def install_root(root=None):
    return host_root(root) / "extensions"


def _read_state_bytes(path):
    if os.name != "nt":
        return Path(path).read_bytes()
    # Python's ordinary Windows open does not share DELETE access. The plugin
    # center polls while the installer atomically replaces this same file;
    # readers must permit replacement and finish reading their old snapshot.
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read()


def read_json(path, default=None):
    with _state_io_lock:
        try:
            return json.loads(_read_state_bytes(path))
        except FileNotFoundError:
            return {} if default is None else default


def write_json(path, value):
    path = Path(path)
    with _state_io_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temp.open("x", encoding="utf-8") as output:
                json.dump(value, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            for attempt in range(20):
                try:
                    os.replace(temp, path)
                    break
                except PermissionError:
                    if os.name != "nt" or attempt == 19:
                        raise
                    # Bounded retry for antivirus/external readers, not a
                    # permission bypass. Persistent denial remains an error.
                    time.sleep(0.05)
        finally:
            temp.unlink(missing_ok=True)


def catalog():
    path = Path(
        os.environ.get("ARGUS_PLUGIN_CATALOG") or Path(__file__).parents[1] / "plugin_catalog.json"
    )
    data = read_json(path, {"plugins": []})
    result = {}
    for entry in data.get("plugins", []):
        if not _NAME.fullmatch(str(entry.get("id", ""))):
            raise PluginError("Invalid plugin catalog id")
        result[entry["id"]] = {**entry, "_catalog_dir": str(path.resolve().parent)}
    return result


def registry(root=None):
    return read_json(install_root(root) / "registry.json")


def preinstalled_ids(env=None):
    """Return the catalog ids the deployment declares, each once, in order."""
    raw = (os.environ if env is None else env).get(PREINSTALL_ENV, "")
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if part and part not in ids:
            ids.append(part)
    return ids


def managed_by_host(name, env=None):
    return name in preinstalled_ids(env)


def state_entry(plugin_id, root=None):
    return registry(root).get(plugin_id, {})


def installed_digest(row, root):
    if row.get("sha256"):
        return row["sha256"]
    if row.get("release"):
        return read_json(_package_path(row, root) / "plugin.json").get("artifact", {}).get("sha256")
    return None


def matches_catalog(row, spec, root):
    """The wheel identity alone does not describe its validated science environment."""
    return (
        row.get("version") == spec["version"]
        and installed_digest(row, root) == spec.get("artifact", {}).get("sha256")
        and row.get("python_constraints", []) == spec.get("python_constraints", [])
        and row.get("validation_imports", []) == spec.get("validation_imports", [])
    )


def _python_install_environment(spec, target):
    constraints = spec.get("python_constraints", [])
    imports = spec.get("validation_imports", [])
    if (not isinstance(constraints, list) or len(constraints) > 32
            or any(not isinstance(item, str) or len(item) > 200
                   or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.<>=!~,*+\[\] -]*", item)
                   for item in constraints)):
        raise PluginError("Invalid scientific Python constraints")
    if (not isinstance(imports, list) or len(imports) > 32
            or any(not isinstance(item, str) or len(item) > 200
                   or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", item)
                   for item in imports)):
        raise PluginError("Invalid scientific validation modules")
    environment = os.environ.copy()
    if constraints:
        constraint_file = target / "python-constraints.txt"
        constraint_file.write_text("\n".join(constraints) + "\n", encoding="utf-8")
        # pip splits PIP_CONSTRAINT on whitespace. A local file URI preserves
        # Windows paths with spaces/Unicode, including in installer child pips.
        environment["PIP_CONSTRAINT"] = " ".join(filter(None, [
            environment.get("PIP_CONSTRAINT", ""), constraint_file.resolve().as_uri(),
        ]))
    return environment


def compatibility(spec, *, env=None, system=None):
    from types import SimpleNamespace

    from ..agent_cli.runner_backend import resolve_available_runner
    from .role_config import resolve_all_roles

    system = system or platform.system().lower()
    environment = env if env is not None else os.environ
    primary = environment.get("ARGUS_SKILL_RUNNER_BACKEND") or "codex"
    try:
        primary, _ = resolve_available_runner(
            primary, environment.get("ARGUS_SKILL_RUNNER_BIN") or None
        )
    except ValueError:
        pass  # Display unsupported configurations instead of breaking discovery.
    roles = [SimpleNamespace(role="default", backend=primary), *resolve_all_roles(env=env)]
    unsupported = {r.role: r.backend for r in roles if r.backend not in spec.get("backends", [])}
    supported = (
        system in spec.get("platforms", [])
        and (
            not spec.get("architectures")
            or platform.machine().lower() in spec["architectures"].get(system, [])
        )
        and not unsupported
        and spec.get("host_api") == API_VERSION
    )
    return {
        "supported": supported,
        "backends": {r.role: r.backend for r in roles},
        "unsupported_roles": unsupported,
        "platform": system,
        "machine": platform.machine().lower(),
        "reason": ""
        if supported
        else UNSUPPORTED
        if unsupported
        else "当前系统或 Argus 插件接口版本暂不支持此插件。",
    }


def _package_path(row, root):
    path = (install_root(root) / row["release"]).resolve()
    if install_root(root) not in path.parents:
        raise PluginError("Invalid installed plugin path")
    return path


def load_plugin(plugin_id, root=None, *, include_disabled=False, _candidate=None):
    root = host_root(root)
    row = _candidate if _candidate is not None else state_entry(plugin_id, root)
    if not row.get("release") or (not include_disabled and not row.get("enabled")):
        return None
    key = (str(root), plugin_id, row["release"])
    with _lock:
        if key in _loaded:
            return _loaded[key]
        directory = _package_path(row, root)
        spec = read_json(directory / "plugin.json")
        module_name, factory = spec["factory"].split(":", 1)
        package_name, _, submodule = module_name.partition(".")
        package = directory / "package" / package_name
        alias = "_argus_ext_" + hashlib.sha256("|".join(key).encode()).hexdigest()[:20]
        module_spec = importlib.util.spec_from_file_location(
            alias, package / "__init__.py", submodule_search_locations=[str(package)]
        )
        if not module_spec or not module_spec.loader:
            raise PluginError("Plugin package is incomplete")
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[alias] = module
        module_spec.loader.exec_module(module)
        plugin = getattr(importlib.import_module(alias + "." + submodule), factory)()
        if plugin.api_version != API_VERSION:
            raise PluginError("Incompatible plugin interface")
        required = (
            "configure_installation",
            "manifest",
            "mount",
            "native_command",
            "prepare_run",
            "finish_run",
            "observe_stream",
            "cancel_operations",
            "accounting_root",
            "vertical_module",
            "owns_workdir",
            "is_busy",
            "shutdown_workers",
        )
        if not all(callable(getattr(plugin, name, None)) for name in required):
            raise PluginError("Plugin does not implement the required host interface")
        plugin.configure_installation(root=root, directory=directory, python=Path(row["python"]))
        if plugin.manifest().get("id") != plugin_id:
            raise PluginError("Plugin identity does not match its catalog entry")
        from .vertical_contract import vertical_contract

        vertical_contract(plugin_id, plugin.vertical_module())
        if plugin_id == "crystalpilot" and os.name == "nt":
            # The published folder picker creates test-projects without parents.
            # Provision only its default host-private parent; never modify the
            # proprietary wheel or create/delete an operator's research folder.
            (root / "crystalpilot-runtime").mkdir(parents=True, exist_ok=True)
        _loaded[key] = plugin
        return plugin


def installed(root=None):
    return {
        name: p
        for name, row in registry(root).items()
        if row.get("enabled") and (p := load_plugin(name, root)) is not None
    }


def _busy(plugin_id, root):
    plugin = load_plugin(plugin_id, root, include_disabled=True)
    if plugin and plugin.is_busy():
        raise PluginError("插件仍有任务运行，请等任务结束或先暂停，再进行此操作。")


def plugin_rows(root=None):
    root = host_root(root)
    managed = set(preinstalled_ids())
    rows = []
    for name, spec in catalog().items():
        state = state_entry(name, root)
        operation = read_json(install_root(root) / name / "operation.json")
        if operation.get("status") == "running" and not _job_alive(root, name, operation):
            _recover_setup(operation)
            operation.update(
                status="failed", error="安装进程已中断；已有可用版本保持不变，可重试。"
            )
            write_json(install_root(root) / name / "operation.json", operation)
        c = compatibility(spec)
        setup = dict(spec.get("setup") or {})
        health = read_json(install_root(root) / name / "resources" / "health.json")
        if health:
            # A failed configure can precede the provider's next health refresh.
            # Label that old snapshot instead of presenting it as this repair's result.
            health["stale"] = (
                operation.get("status") == "failed"
                and operation.get("started", 0) > health.get("checked", 0)
            )
        if name == "crystalpilot" and os.name == "nt":
            from .platon_windows import LICENSE_NOTICE, OFFICIAL_PAGE

            setup["windows_runtime"] = {
                "action": "platon_runtime", "name": "PLATON",
                "url": OFFICIAL_PAGE, "notice": LICENSE_NOTICE,
                "accepted": _platon_license_accepted(root),
            }
            for component in health.get("components", []):
                if component.get("id") == "platon" and any(
                    code in str(component.get("detail", ""))
                    for code in ("3221225781", "0xC0000135")
                ):
                    component["detail"] = (
                        "PLATON 缺少 Windows Salford 运行库（0xC0000135）。"
                        "请点击“修复依赖”，确认许可后会在同一流程准备完整运行环境。"
                    )
        rows.append(
            {
                **{
                    k: v
                    for k, v in spec.items()
                    if not k.startswith("_") and k not in {"artifact", "factory"}
                },
                **c,
                "installed": bool(state.get("release")),
                "enabled": bool(state.get("enabled")),
                "installed_version": state.get("version"),
                "update_available": bool(state.get("version") and not matches_catalog(state, spec, root)),
                "environment_update": bool(state.get("version") == spec["version"]
                                           and not matches_catalog(state, spec, root)),
                "operation": operation,
                "setup": setup,
                "health": health,
                "available": c["supported"] and bool(state.get("enabled")),
                "managed_by_host": name in managed,
                "url": f"/plugins/{name}/",
            }
        )
        if operation.get("status") == "running":
            progress = read_json(install_root(root) / name / "progress.json")
            if progress.get("updated", 0) >= operation.get("started", 0):
                operation["progress"] = progress.get("progress", operation.get("progress"))
    return rows


def _job_alive(root, name, operation):
    from .process_identity import process_identity_is_running

    thread = _jobs.get((str(root), name))
    if operation.get("pid") == os.getpid():
        # A polling request can arrive between publishing the operation and
        # Thread.start(); that is not an interrupted installer.
        return bool(thread and (thread.is_alive() or thread.ident is None))
    return process_identity_is_running(operation.get("pid", 0), operation.get("identity"))


def _recover_setup(operation):
    """Reap only our identified orphan installer before permitting a retry."""
    from .process_identity import process_identity_is_running

    pid = operation.get("installer_pid", 0)
    if not process_identity_is_running(pid, operation.get("installer_identity")):
        return
    import psutil

    try:
        parent = psutil.Process(pid)
        processes = [*parent.children(recursive=True), parent]
        for process in processes:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(processes, timeout=3)
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass


def _python(root=None):
    explicit = os.environ.get("ARGUS_PLUGIN_PYTHON")
    candidates = [[explicit]] if explicit else []
    if not explicit:
        if not getattr(sys, "frozen", False):
            candidates.append([sys.executable])
        if os.name == "nt" and shutil.which("py"):
            candidates.extend([["py", "-" + version] for version in ("3.11", "3.12", "3.13")])
        candidates.extend(
            [
                [p]
                for p in ("python3.11", "python3.12", "python3.13", "python3", "python")
                if shutil.which(p)
            ]
        )
    for command in candidates:
        try:
            result = subprocess.run(
                [
                    *command,
                    "-I",
                    "-c",
                    "import sys; assert not getattr(sys, 'frozen', False); "
                    "assert (3,11)<=sys.version_info[:2]<(3,14); "
                    "import venv, ensurepip; print(sys.executable)",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
    if root is not None and not explicit:
        from .plugin_runtime import portable_python

        return portable_python(install_root(root) / "runtime")
    raise PluginError(
        "安装科学环境需要 Python 3.11–3.13。请安装 Python 后重试，或设置 ARGUS_PLUGIN_PYTHON。"
    )


def _fetch(spec, destination):
    artifact = spec.get("artifact") or {}
    checksum = artifact.get("sha256", "")
    if not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise PluginError("此插件版本尚未提供可校验的发行包。")
    local = artifact.get("local")
    if local:
        source = (Path(spec["_catalog_dir"]) / local).resolve()
        shutil.copyfile(source, destination)
    else:
        url = artifact.get("url", "")
        if not url.startswith("https://"):
            raise PluginError("插件发行包尚未发布。")
        with httpx.stream("GET", url, follow_redirects=True, timeout=90) as response:
            response.raise_for_status()
            with destination.open("wb") as target:
                size = 0
                for part in response.iter_bytes():
                    size += len(part)
                    if size > 256 * 1024 * 1024:
                        raise PluginError("插件包超过允许大小。")
                    target.write(part)
    if hashlib.sha256(destination.read_bytes()).hexdigest() != checksum:
        raise PluginError("插件包校验失败，未安装。")


def _extract(wheel, destination):
    with zipfile.ZipFile(wheel) as archive:
        if sum(i.file_size for i in archive.infolist()) > 512 * 1024 * 1024:
            raise PluginError("Plugin package expands beyond its size limit")
        for entry in archive.infolist():
            path = (destination / entry.filename).resolve()
            if (
                destination.resolve() not in path.parents
                or (entry.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise PluginError("Unsafe path in plugin package")
        archive.extractall(destination)


def _platon_license_accepted(root):
    from .platon_windows import LICENSE_NOTICE, OFFICIAL_PAGE

    resources = install_root(root) / "crystalpilot" / "resources"
    notice = hashlib.sha256((OFFICIAL_PAGE + LICENSE_NOTICE).encode()).hexdigest()
    consent = read_json(resources / "host-platon-consent.json")
    if consent.get("accepted") is True and consent.get("notice_sha256") == notice:
        return True
    previous = read_json(resources / "host-platon-runtime.json")
    return previous.get("license_accepted") is True and previous.get("source") == OFFICIAL_PAGE


def _remember_platon_consent(root):
    from .platon_windows import LICENSE_NOTICE, OFFICIAL_PAGE

    write_json(install_root(root) / "crystalpilot" / "resources" / "host-platon-consent.json", {
        "accepted": True, "accepted_at": time.time(), "source": OFFICIAL_PAGE,
        "notice_sha256": hashlib.sha256((OFFICIAL_PAGE + LICENSE_NOTICE).encode()).hexdigest(),
    })


def _prepare_platon_for_setup(root):
    from .platon_windows import prepare, probe
    from .process_identity import capture_process_identity

    directory = install_root(root) / "crystalpilot"
    resources = directory / "resources"

    def progress(message):
        write_json(directory / "progress.json", {"progress": message, "updated": time.time()})

    def started(pid):
        path = directory / "operation.json"
        current = read_json(path)
        current.update(installer_pid=pid, installer_identity=capture_process_identity(pid))
        write_json(path, current)

    # Keep a user's already-working installation instead of replacing it merely
    # because its executable did not come from our own preparation recipe.
    existing = read_json(resources / "software.json").get("platon", {}).get("path")
    managed = read_json(resources / "host-platon-runtime.json").get("path")
    # Our own installation must also match the newly shipped adapter. A clean
    # probe of the old adapter alone would otherwise prevent its upgrade forever.
    host_managed = bool(existing and managed and Path(existing).resolve() == Path(managed).resolve())
    if existing and not host_managed:
        try:
            probe(Path(existing), on_start=started)
            progress("PLATON 已可用，保留当前安装。")
            return Path(existing)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            pass
    return prepare(resources, accept_license=True, progress=progress, on_start=started)


def _check_setup_health(name, root, *, only_platon=False):
    health = read_json(install_root(root) / name / "resources" / "health.json")
    components = health.get("components", [])
    expected = {"platon"} if only_platon else {"python", "dials", "systre", "platon"}
    states = {row.get("id"): row.get("status") for row in components}
    missing = sorted(key for key in expected if states.get(key) != "ready")
    if missing:
        raise PluginError("依赖修复未完成：" + ", ".join(missing) + "。请查看组件检查结果；不会报告修复成功。")
    return health


def _run_setup(name, spec, root, python, action, payload=None):
    """One path for automatic installation, Repair and the legacy runtime action."""
    payload = dict(payload or {})
    windows_platon = name == "crystalpilot" and os.name == "nt"
    if windows_platon and action in {"repair", "platon_runtime"}:
        if payload.get("accept_software_license") is True:
            _remember_platon_consent(root)
        if not _platon_license_accepted(root):
            raise PluginError("请先确认 PLATON 使用许可，再继续安装或修复依赖。")
        executable = _prepare_platon_for_setup(root)
        # Configure before the published repair routine runs. Its own PLATON
        # probe now succeeds, so it never repeats the incomplete EXE-only recipe.
        _invoke_setup(name, spec, root, python, "configure", {"paths": {"platon": str(executable)}})
        if action == "repair":
            _invoke_setup(name, spec, root, python, "repair", payload)
        return _check_setup_health(name, root, only_platon=action == "platon_runtime")
    return _invoke_setup(name, spec, root, python, action, payload)


def _invoke_setup(name, spec, root, python, action, payload=None):
    from .plugin_runtime import clean_env, run

    module = spec.get("setup", {}).get("module")
    if not module:
        return
    directory = install_root(root) / name
    env = clean_env()
    env["ARGUS_PLUGIN_PROGRESS_FILE"] = str(directory / "progress.json")

    def record(pid):
        from .process_identity import capture_process_identity

        path = directory / "operation.json"
        state = read_json(path)
        state.update(installer_pid=pid, installer_identity=capture_process_identity(pid))
        write_json(path, state)

    output = run(
        [python, "-m", module, "--root", directory / "resources", "--action", action],
        env=env,
        input=json.dumps(payload or {}),
        timeout=7200 if action == "repair" else 900,
        on_start=record,
    )
    # This is our typed installer output, not a shell command containing secrets.
    with (directory / "install.log").open("a", encoding="utf-8") as log:
        log.write(output + "\n")


def _install(name, spec, root, action="install"):
    from .process_identity import capture_process_identity

    operation_path = install_root(root) / name / "operation.json"
    operation = {
        "status": "running",
        "action": action,
        "progress": "准备安装",
        "started": time.time(),
        "pid": os.getpid(),
        "identity": capture_process_identity(os.getpid()),
    }
    write_json(operation_path, operation)
    release = (
        Path(name)
        / "releases"
        / (spec["version"] + "-" + spec["artifact"]["sha256"][:12] + "-" + uuid.uuid4().hex[:8])
    )
    target = install_root(root) / release
    try:
        target.mkdir(parents=True)
        operation["progress"] = "获取并校验插件包"
        write_json(operation_path, operation)
        wheel = target / spec["artifact"]["filename"]
        if Path(wheel.name).name != spec["artifact"]["filename"] or not wheel.name.endswith(".whl"):
            raise PluginError("Invalid wheel filename")
        _fetch(spec, wheel)
        _extract(wheel, target / "package")
        write_json(target / "plugin.json", spec)
        install_env = _python_install_environment(spec, target)
        operation["progress"] = "安装独立运行环境（首次可能需要几分钟）"
        write_json(operation_path, operation)
        executable = _python(root)
        science = target / "science"
        py = science / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        log = install_root(root) / name / "install.log"
        with log.open("ab") as output:
            subprocess.run(
                [executable, "-m", "venv", str(science)],
                check=True,
                stdout=output,
                stderr=output,
                timeout=180,
            )
            subprocess.run(
                [str(py), "-m", "pip", "install", str(wheel)],
                check=True,
                stdout=output,
                stderr=output,
                timeout=900,
                env=install_env,
            )
            subprocess.run(
                [str(py), "-m", spec["installer"], "--prefix", str(science)],
                check=True,
                stdout=output,
                stderr=output,
                timeout=1800,
                env=install_env,
            )
            subprocess.run(
                [str(py), "-m", spec["installer"], "--check"],
                check=True,
                stdout=output,
                stderr=output,
                timeout=120,
            )
            operation["progress"] = "验证科学环境依赖和关键模块"
            write_json(operation_path, operation)
            subprocess.run([str(py), "-m", "pip", "check"], check=True,
                           stdout=output, stderr=output, timeout=120)
            if spec.get("validation_imports"):
                subprocess.run([
                    str(py), "-I", "-X", "faulthandler", "-c",
                    "import importlib,sys; [importlib.import_module(name) for name in sys.argv[1:]]",
                    *spec["validation_imports"],
                ], check=True, stdout=output, stderr=output, timeout=120)
        if spec.get("setup", {}).get("automatic"):
            _run_setup(name, spec, root, str(py), "repair")
        candidate = {
            "version": spec["version"],
            "release": str(release),
            "python": str(py),
            "enabled": True,
            "installed": time.time(),
            "sha256": spec["artifact"]["sha256"],
            "python_constraints": spec.get("python_constraints", []),
            "validation_imports": spec.get("validation_imports", []),
        }
        # Validate import, interface, identity and vertical on the current host
        # before changing the active registry or stopping the old worker.
        load_plugin(name, root, include_disabled=True, _candidate=candidate)
        _busy(name, root)
        old = load_plugin(name, root, include_disabled=True)
        if old:
            old.shutdown_workers()
        registry_path = install_root(root) / "registry.json"
        with portalocker.Lock(str(registry_path.with_suffix(".lock")), timeout=30):
            data = registry(root)
            data[name] = candidate
            write_json(registry_path, data)
        operation.update(status="completed", progress="安装完成", completed=time.time())
    except Exception as exc:
        operation.update(
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            progress="安装未完成，已有版本保持不变",
            completed=time.time(),
        )
        # Keep the log but discard this unusable version. Never touch research data.
        shutil.rmtree(target, ignore_errors=True)
    finally:
        write_json(operation_path, operation)


def _setup_job(name, spec, root, action, payload):
    path = install_root(root) / name / "operation.json"
    operation = read_json(path)
    if action == "repair":
        from .plugin_runtime import run

        try:
            run(
                [
                    state_entry(name, root)["python"],
                    "-I",
                    "-c",
                    "import " + spec["setup"]["module"],
                ],
                timeout=30,
            )
        except (OSError, RuntimeError, subprocess.SubprocessError):
            # A deleted interpreter or damaged bridge cannot repair itself.
            # Recreate its version environment through the normal verified flow.
            payload.clear()
            _install(name, spec, root, action="repair")
            return
    try:
        row = state_entry(name, root)
        if action != "health":
            plugin = load_plugin(name, root, include_disabled=True)
            if plugin:
                _busy(name, root)
                plugin.shutdown_workers()
        _run_setup(name, spec, root, row["python"], action, payload)
        operation.update(status="completed", progress="依赖修复完成" if action in {"repair", "platon_runtime"} else "环境检查完成", completed=time.time())
    except Exception as exc:
        error = str(exc)
        for secret in (payload.get("username"), payload.get("password")):
            if secret:
                error = error.replace(secret, "[redacted]")
        operation.update(
            status="failed", error=error[-1600:],
            progress="依赖修复未完成" if action in {"repair", "platon_runtime"} else "环境操作未完成",
            completed=time.time(),
        )
    finally:
        payload.clear()
        write_json(path, operation)


def _start_job(root, name, action, target, args):
    from .process_identity import capture_process_identity

    job = threading.Thread(target=target, args=args, daemon=True, name=f"plugin-{action}-{name}")
    _jobs[(str(root), name)] = job
    write_json(
        install_root(root) / name / "operation.json",
        {
            "status": "running",
            "action": action,
            "progress": "正在准备",
            "started": time.time(),
            "pid": os.getpid(),
            "identity": capture_process_identity(os.getpid()),
        },
    )
    try:
        job.start()
    except RuntimeError as exc:
        _jobs.pop((str(root), name), None)
        write_json(install_root(root) / name / "operation.json", {
            "status": "failed", "action": action, "error": "无法启动安装线程，请稍后重试。",
        })
        raise PluginError("无法启动安装线程，请稍后重试。") from exc
    return {"status": "running"}


def mutate(name, action, root=None, *, payload=None):
    root = host_root(root)
    spec = catalog().get(name)
    if not spec:
        raise PluginError("Unknown plugin")
    directory = install_root(root) / name
    directory.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(directory / "manage.lock"), timeout=5):
        operation = read_json(directory / "operation.json")
        if operation.get("status") == "running" and _job_alive(root, name, operation):
            raise PluginError("此插件已有安装或更新操作正在进行。")
        if operation.get("status") == "running":
            _recover_setup(operation)
        if action != "health":
            _busy(name, root)
        row = state_entry(name, root)
        if name == "crystalpilot" and os.name == "nt" and action in {"install", "update", "repair", "platon_runtime"}:
            if (payload or {}).get("accept_software_license") is True:
                _remember_platon_consent(root)
            if not _platon_license_accepted(root):
                raise PluginError("请先确认 PLATON 使用许可，再继续安装或修复依赖。")
        if action in {"install", "update"}:
            c = compatibility(spec)
            if not c["supported"]:
                raise PluginError(c["reason"])
            if action == "update" and not row.get("release"):
                raise PluginError("插件尚未安装")
            if matches_catalog(row, spec, root):
                raise PluginError("此版本已安装，可启用插件。")
            return _start_job(root, name, action, _install, (name, spec, root, action))
        if action == "platon_runtime":
            if name != "crystalpilot" or os.name != "nt":
                raise PluginError("此官方运行环境准备操作仅适用于 Windows CrystalPilot。")
            if not row.get("release"):
                raise PluginError("请先安装插件")
            installed_spec = read_json(_package_path(row, root) / "plugin.json")
            return _start_job(root, name, action, _setup_job,
                              (name, installed_spec, root, action, dict(payload or {})))
        if action in spec.get("setup", {}).get("actions", []):
            if not row.get("release"):
                raise PluginError("请先安装插件")
            # Invoke the installed module/contract, not a newer catalog's code.
            installed_spec = read_json(_package_path(row, root) / "plugin.json")
            if action not in installed_spec.get("setup", {}).get("actions", []):
                raise PluginError("请先更新插件以使用环境管理功能")
            return _start_job(
                root,
                name,
                action,
                _setup_job,
                (name, installed_spec, root, action, dict(payload or {})),
            )
        if action not in {"enable", "disable", "uninstall"}:
            raise PluginError("Unknown plugin operation")
        if action != "enable" and managed_by_host(name):
            raise PluginError(HOST_MANAGED)
        if not row.get("release"):
            raise PluginError("插件尚未安装")
        if action == "enable" and not compatibility(spec)["supported"]:
            raise PluginError(compatibility(spec)["reason"])
        plugin = load_plugin(name, root, include_disabled=True)
        if action in {"disable", "uninstall"} and plugin:
            plugin.shutdown_workers()
        registry_path = install_root(root) / "registry.json"
        with portalocker.Lock(str(registry_path.with_suffix(".lock")), timeout=30):
            data = registry(root)
            if action == "uninstall":
                data.pop(name, None)
            else:
                data[name]["enabled"] = action == "enable"
            write_json(registry_path, data)
        if action == "uninstall":
            shutil.rmtree(directory / "releases", ignore_errors=True)
        return {"status": "completed", "data_retained": True}


def preinstall_need(name, root=None):
    """Return what a declared plugin still needs: install, update, enable, or None."""
    spec = catalog().get(name)
    if not spec:
        raise PluginError("Unknown plugin")
    row = state_entry(name, root)
    if not row.get("release"):
        return "install"
    if not matches_catalog(row, spec, root):
        return "update"
    if not row.get("enabled"):
        return "enable"
    return None


def wait_for_operation(name, root=None, timeout=10800):
    """Block until the plugin's running operation ends and return its final record."""
    root = host_root(root)
    deadline = time.monotonic() + timeout
    thread = _jobs.get((str(root), name))
    if thread is not None:
        thread.join(timeout)
    while True:
        operation = read_json(install_root(root) / name / "operation.json")
        if operation.get("status") != "running" or not _job_alive(root, name, operation):
            return operation
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Plugin {name} did not finish within {timeout:.0f} seconds")
        time.sleep(1)


def preinstall(root=None, *, ids=None, wait=False, timeout=10800, logger=None):
    """Bring every declared plugin to installed, enabled and current.

    The web server calls this once at startup on its own thread, so a slow
    download never delays the interface; the image-build command calls it with
    ``wait`` so the shared root is complete before the image is sealed. A
    plugin that is already current is left untouched, and one whose operation
    is still running is left to finish rather than started twice. Returns one
    record per id with a ``status`` of ready, running, completed or failed.
    """
    logger = logger or log
    root = host_root(root)
    results = {}
    for name in preinstalled_ids() if ids is None else list(ids):
        try:
            need = preinstall_need(name, root)
        except PluginError as exc:
            logger.warning("Plugin %s cannot be prepared: %s", name, exc)
            results[name] = {"status": "failed", "error": str(exc)}
            continue
        if need is None:
            logger.info("Plugin %s is installed, enabled and current under %s", name, root)
            results[name] = {"status": "ready"}
            continue
        operation = read_json(install_root(root) / name / "operation.json")
        if operation.get("status") == "running" and _job_alive(root, name, operation):
            action = operation.get("action", need)
            logger.info("Plugin %s already has a %s operation running; leaving it to finish", name, action)
            record = {"status": "running", "action": action}
        else:
            try:
                outcome = mutate(name, need, root)
            except PluginError as exc:
                logger.warning("Plugin %s could not start its %s: %s", name, need, exc)
                results[name] = {"status": "failed", "action": need, "error": str(exc)}
                continue
            record = {"status": outcome["status"], "action": need}
            logger.info(
                "Plugin %s: %s %s under %s",
                name,
                "started" if record["status"] == "running" else "finished",
                need,
                root,
            )
        if wait and record["status"] == "running":
            final = wait_for_operation(name, root, timeout)
            record["status"] = "completed" if final.get("status") == "completed" else "failed"
            if final.get("error"):
                record["error"] = final["error"]
            (logger.info if record["status"] == "completed" else logger.warning)(
                "Plugin %s: %s %s%s",
                name,
                record["action"],
                record["status"],
                f" ({record['error']})" if record.get("error") else "",
            )
        results[name] = record
    return results
