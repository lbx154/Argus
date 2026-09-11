"""Runtime hooks for explicitly installed and enabled workbench plugins."""

from pathlib import Path

from . import plugin_manager as manager


def installed_workbenches():
    return manager.installed()


def native_plugin_command(text, *, sid, life_dir, global_root):
    command = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    for name, spec in manager.catalog().items():
        if command.lower() != spec.get("command"):
            continue
        plugin = manager.load_plugin(name, global_root)
        if plugin is None:
            return "请先在 Argus 的“插件”页面安装并启用 " + spec["name"] + "。"
        compatible = manager.compatibility(spec)
        # Closing an old binding is always allowed; it invokes no model/tools.
        if not compatible["supported"] and text.strip().split(maxsplit=1)[-1] not in {
            "off",
            "关闭",
            "status",
            "状态",
        }:
            return compatible["reason"]
        return plugin.native_command(text, sid=sid, life_dir=life_dir, global_root=global_root)
    return None


def prepare_plugin_run(prompt, options, *, backend, run_label, project_root=None):
    import portalocker

    if options is None or project_root is None:
        return prompt, options
    project_root = Path(project_root).resolve()
    for name, plugin in installed_workbenches().items():
        spec = manager.catalog()[name]
        bindings_path = manager.host_root() / spec["session_bindings"]
        binding = manager.read_json(bindings_path).get(project_root.name, {})
        if not binding.get("enabled") or Path(binding["life_dir"]).resolve() != project_root:
            continue
        directory = manager.install_root() / name
        with portalocker.Lock(str(directory / "manage.lock"), timeout=10):
            # The external plugin owns this registry. A shared working directory
            # is not authorization to use another session's scientific dataset.
            bindings = manager.read_json(bindings_path)
            binding = bindings.get(project_root.name, {})
            if not binding.get("enabled") or Path(binding["life_dir"]).resolve() != project_root:
                continue
            # The registry may have switched after the initial discovery.
            plugin = manager.load_plugin(name)
            if plugin is None:
                raise manager.PluginError("插件已停用，请重新启用后再执行。")
            if not plugin.owns_workdir(options.working_dir):
                continue
            operation = manager.read_json(directory / "operation.json")
            if (
                operation.get("status") == "running"
                and operation.get("action") != "health"
                and manager._job_alive(manager.host_root(), name, operation)
            ):
                # Only block a call actually bound to this plugin. Ordinary
                # Argus sessions continue while an optional plugin updates.
                if plugin.owns_workdir(options.working_dir):
                    raise manager.PluginError("插件正在更新，请稍候再开始新的任务。")
                continue
            prompt, options = plugin.prepare_run(
                prompt, options, backend=backend, run_label=run_label
            )
    return prompt, options


def finish_plugin_run(options):
    for plugin in installed_workbenches().values():
        plugin.finish_run(options)


def observe_plugin_stream(options, stream, line):
    for plugin in installed_workbenches().values():
        plugin.observe_stream(options, stream, line)


def cancel_plugin_operations(sid):
    for plugin in installed_workbenches().values():
        plugin.cancel_operations(sid)


def plugin_accounting_root(project_root):
    for plugin in installed_workbenches().values():
        root = plugin.accounting_root(project_root)
        if root is not None:
            return root
    return None
