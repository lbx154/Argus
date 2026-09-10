"""Optional workbench installation, isolated packages and atomic activation.

The catalog is host-owned release metadata. The browser selects an id, never an
arbitrary URL, command or Python package. Research data is not an install payload.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
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
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_loaded: dict[tuple[str, str, str], object] = {}
_jobs: dict[tuple[str, str], threading.Thread] = {}
_lock = threading.RLock()


class PluginError(ValueError):
    pass


def host_root(root=None):
    return Path(root or os.environ.get("ARGUS_WORKBENCH_HOST_ROOT") or global_root()).resolve()


def install_root(root=None):
    return host_root(root) / "extensions"


def read_json(path, default=None):
    if not Path(path).exists():
        return {} if default is None else default
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


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


def state_entry(plugin_id, root=None):
    return registry(root).get(plugin_id, {})


def installed_digest(row, root):
    if row.get("sha256"):
        return row["sha256"]
    if row.get("release"):
        return read_json(_package_path(row, root) / "plugin.json").get("artifact", {}).get("sha256")
    return None


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
                "update_available": bool(
                    state.get("version")
                    and (
                        state.get("version") != spec["version"]
                        or installed_digest(state, root) != spec.get("artifact", {}).get("sha256")
                    )
                ),
                "operation": operation,
                "health": read_json(install_root(root) / name / "resources" / "health.json"),
                "available": c["supported"] and bool(state.get("enabled")),
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
        return bool(thread and thread.is_alive())
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
                    "import sys; assert (3,11)<=sys.version_info[:2]<(3,14); print(sys.executable)",
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


def _run_setup(name, spec, root, python, action, payload=None):
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
            )
            subprocess.run(
                [str(py), "-m", spec["installer"], "--prefix", str(science)],
                check=True,
                stdout=output,
                stderr=output,
                timeout=1800,
            )
            subprocess.run(
                [str(py), "-m", spec["installer"], "--check"],
                check=True,
                stdout=output,
                stderr=output,
                timeout=120,
            )
        if spec.get("setup", {}).get("automatic"):
            _run_setup(name, spec, root, str(py), "repair")
        candidate = {
            "version": spec["version"],
            "release": str(release),
            "python": str(py),
            "enabled": True,
            "installed": time.time(),
            "sha256": spec["artifact"]["sha256"],
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
        operation.update(status="completed", progress="环境检查完成", completed=time.time())
    except Exception as exc:
        error = str(exc)
        for secret in (payload.get("username"), payload.get("password")):
            if secret:
                error = error.replace(secret, "[redacted]")
        operation.update(status="failed", error=error[-1600:], completed=time.time())
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
    job.start()
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
        if action in {"install", "update"}:
            c = compatibility(spec)
            if not c["supported"]:
                raise PluginError(c["reason"])
            if action == "update" and not row.get("release"):
                raise PluginError("插件尚未安装")
            if row.get("version") == spec["version"] and installed_digest(row, root) == spec.get(
                "artifact", {}
            ).get("sha256"):
                raise PluginError("此版本已安装，可启用插件。")
            return _start_job(root, name, action, _install, (name, spec, root, action))
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
