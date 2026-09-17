"""Read scoped runtime facts without a provider call or process control."""
from __future__ import annotations

import os
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

STATUS_MODES = frozenset({"project_status", "argus_status", "host_status"})
_WEB_READERS: dict[Path, dict[object, Callable[[str], int | None]]] = {}
_LOCK = threading.RLock()
_PROJECT_LIMIT = 128


def register_web_status(root: Path, requests: Callable[[str], int | None]) -> Callable[[], None]:
    """A running WebAPI supplies live foreground activity, scoped to its root."""
    root, registration = root.resolve(), object()
    with _LOCK:
        _WEB_READERS.setdefault(root, {})[registration] = requests

    def close() -> None:
        with _LOCK:
            readers = _WEB_READERS.get(root, {})
            readers.pop(registration, None)
            if not readers:
                _WEB_READERS.pop(root, None)

    return close


def _project_status(root: Path, readers: list[Callable[[str], int | None]]) -> dict[str, Any]:
    from ..core.session import read_session_meta
    from ..daemon.state import read_continuous_state, read_daemon_status
    from ..life.memory import Backlog

    row: dict[str, Any] = {"id": root.name, "state": "unknown"}
    try:
        daemon = read_daemon_status(root)
        continuous = read_continuous_state(root)
        tasks = Backlog(root / "backlog.jsonl").active()
        meta = read_session_meta(root.parent.parent, root.name) if root.parent.name == "projects" else None
        observations = [reader(root.name) for reader in readers]
        foreground = sum(value for value in observations if value is not None) if observations and all(value is not None for value in observations) else None
        live = [item for item in tasks if item.status == "running"]
        pending = [item for item in tasks if item.status == "pending"]
        other = [item for item in tasks if item.status not in {"running", "pending", "done", "failed", "aborted", "skipped", "superseded"}]
        row.update(
            name=(meta.display_name if meta else "") or root.name,
            daemon_alive=bool(daemon.alive),
            daemon_started=(root / "daemon.status.json").exists(),
            foreground_requests=foreground,
            continuous_enabled=bool(continuous.enabled),
            objective=str(continuous.objective or (meta.objective if meta else "")),
            running_tasks=[item.title for item in live][:5],
            queued_tasks=len(pending),
            waiting_tasks=len(other),
            # Backlog claims alone never establish a live worker. Conversely,
            # SELF calls may be executing while the task daemon is absent.
            state=("active" if daemon.alive or foreground else
                   "interrupted" if live else "waiting" if pending or other or continuous.enabled else
                   "idle" if foreground is not None else "unobserved"),
        )
    except Exception:  # noqa: BLE001 - unreadable facts must remain unknown
        row["error"] = "project status could not be fully read"
    return row


def observe_runtime_status(project_root: Path | str, mode: str) -> dict[str, Any]:
    if mode not in STATUS_MODES:
        raise ValueError("unknown status scope")
    raw = Path(project_root).expanduser()
    root = raw.resolve()
    # A project cannot widen its scope through a symlink or an arbitrary parent.
    owner = root.parent.parent if raw.parent.name == "projects" and not raw.is_symlink() and not raw.parent.is_symlink() else None
    with _LOCK:
        readers = list(_WEB_READERS.get(owner, {}).values()) if owner else []
    result: dict[str, Any] = {
        "scope": mode, "observed_at": time.time(),
        "service": {"webapi": "running" if readers else "unobserved", "pid": os.getpid() if readers else None},
        "current_project": _project_status(root, readers),
        "projects": [], "project_scan_complete": False,
    }
    if mode != "project_status" and owner is not None:
        try:
            paths = []
            for path in (owner / "projects").iterdir():
                if path.is_symlink() or not path.is_dir():
                    continue
                paths.append(path)
                if len(paths) > _PROJECT_LIMIT:
                    break
            result["project_scan_complete"] = len(paths) <= _PROJECT_LIMIT
            result["projects"] = [
                result["current_project"] if path == root else _project_status(path, readers)
                for path in paths[:_PROJECT_LIMIT]
            ]
        except OSError:
            result["project_scan_error"] = True
    if mode == "host_status":
        try:
            import psutil  # type: ignore[import-untyped]  # Runtime dependency has no bundled stubs.

            memory = psutil.virtual_memory()
            counts: Counter[str] = Counter()
            inaccessible = 0
            account = psutil.Process().username()
            for process in psutil.process_iter(["name", "username"], ad_value=None):
                try:
                    if process.info["username"] is None:
                        inaccessible += 1
                    elif process.info["username"] == account:
                        counts[str(process.info["name"] or "unknown")] += 1
                except (psutil.Error, OSError):
                    inaccessible += 1
            result["host"] = {
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "memory_percent": memory.percent,
                "account_process_count": sum(counts.values()),
                "process_names": dict(counts.most_common(6)),
                "unreadable_processes": inaccessible,
                "scope": "system resource totals; process names for the service account only",
            }
        except Exception:  # noqa: BLE001 - missing or denied host facts are unknown
            result["host"] = {"error": "host status unavailable"}
    return result


def render_runtime_status(facts: dict[str, Any], *, chinese: bool) -> str:
    """Render only observed facts, keeping unknown and idle distinct."""
    from ..core.secret_guard import known_secret_values, redact_secrets_text

    def title(value: Any) -> str:
        return " ".join(str(value or "").split()).replace("`", "")[:120]

    row = facts["current_project"]
    lines = []
    if facts["service"]["webapi"] == "running":
        lines.append("网页服务正在运行，当前正在响应你的状态查询。" if chinese else "The web service is running and handling this status request.")
    else:
        lines.append("本次没有获取到网页服务的运行状态。" if chinese else "The web service's state was not observed in this check.")
    if row.get("error"):
        lines.append("当前项目的状态未能完整读取，暂时不能判断是否空闲。" if chinese else "Current project status could not be fully read; its activity is unknown.")
    else:
        state = ("正在运行" if row["daemon_alive"] else "未启动" if not row["daemon_started"] else "已退出") if chinese else (
            "running" if row["daemon_alive"] else "not started" if not row["daemon_started"] else "exited")
        lines.append(f"当前项目的后台执行进程{state}。" if chinese else f"Current project's background worker: {state}.")
        if row["running_tasks"]:
            label = "正在执行" if row["daemon_alive"] else "记录中尚未结束，执行进程当前不在线"
            lines.append((label + "：" if chinese else ("Running: " if row["daemon_alive"] else "Unfinished in the backlog; worker offline: ")) + "；".join(title(item) for item in row["running_tasks"]) + "。")
        elif row["queued_tasks"] or row["waiting_tasks"] or row["continuous_enabled"]:
            lines.append((f"还有 {row['queued_tasks']} 项待执行、{row['waiting_tasks']} 项等待处理。" if chinese else f"Queued: {row['queued_tasks']}; waiting: {row['waiting_tasks']}.") + title(row.get("objective")))
        else:
            lines.append("当前项目没有待执行的后台任务。" if chinese else "This project has no queued background tasks.")
        foreground = row.get("foreground_requests")
        if foreground:
            lines.append(f"网页前台有 {foreground} 个请求正在处理，包含本次查询；这不依赖后台执行进程。" if chinese else f"The web service is handling {foreground} foreground request(s), including this query; these do not require the background worker.")
        elif foreground is None:
            lines.append("其他前台请求的活动未观测到。" if chinese else "Other foreground request activity was not observed.")
    if facts["scope"] == "project_status":
        lines.append("以上仅描述当前项目，未检查其他项目或整台主机。" if chinese else "This describes only the current project; other projects and the host were not checked.")
    else:
        peers = [item for item in facts["projects"] if item["id"] != row["id"]]
        counts = Counter(item["state"] for item in peers)
        lines.append((f"本 Argus 实例的其他 {len(peers)} 个项目：{counts['active']} 个正在运行或处理请求，{counts['waiting'] + counts['interrupted']} 个有待处理工作，{counts['idle']} 个空闲，{counts['unknown'] + counts['unobserved']} 个状态未完全确认。" if chinese else f"Other projects in this Argus instance: {len(peers)} checked; {counts['active']} active, {counts['waiting'] + counts['interrupted']} with waiting or interrupted work, {counts['idle']} idle, {counts['unknown'] + counts['unobserved']} not fully observed."))
        for item in [peer for peer in peers if peer["state"] in {"active", "waiting", "interrupted"}][:5]:
            state = {"active": "运行或处理请求中", "waiting": "等待处理", "interrupted": "执行中断"}[item["state"]] if chinese else item["state"]
            lines.append(f"- {title(item.get('name', item['id']))}：{state}" + ("；" + title(item["running_tasks"][0]) if item.get("running_tasks") else ""))
        if not facts["project_scan_complete"]:
            lines.append("项目列表没有完整读取，不能据此断言所有项目空闲。" if chinese else "The project scan is incomplete; this cannot establish that all projects are idle.")
        if facts["scope"] != "host_status":
            lines.append("以上不代表主机上的其他 Argus 实例或其他程序。" if chinese else "This does not describe other Argus instances or other host programs.")
    host = facts.get("host")
    if host is not None:
        if host.get("error"):
            lines.append("主机状态读取失败，不能判断整台服务器在做什么。" if chinese else "Host status could not be read; server activity is unknown.")
        else:
            programs = "、".join(f"{title(name)} × {count}" for name, count in host["process_names"].items())
            lines.append(f"主机即时 CPU 使用率 {host['cpu_percent']:.1f}%，内存使用率 {host['memory_percent']:.1f}%。服务账号可见 {host['account_process_count']} 个进程，包括 {programs}。" if chinese else f"Host CPU: {host['cpu_percent']:.1f}%; memory: {host['memory_percent']:.1f}%. Service-account processes: {host['account_process_count']} ({programs}).")
            lines.append("这些进程不等于 Argus 子代理；其他程序的具体任务未核实，未读取其他账号的进程详情。" if chinese else "These processes are not all Argus agents. Other programs' tasks were not verified; other accounts' process details were not read.")
    return redact_secrets_text("\n\n".join(lines), known_values=known_secret_values())
