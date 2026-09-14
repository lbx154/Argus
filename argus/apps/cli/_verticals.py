"""``argus verticals``: the Vertical Store from the command line.

Synchronous by design: every job waits for its operation record to finish and
prints one line per progress change, so a transcript reads top to bottom.
Exit status 0 on success, 1 when the store refuses or a job fails, 2 for a
path-resolution error (handled by the caller) or a bad argument.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ...verticals import store

_LIST_COLUMNS = ("name", "kind", "version", "installed", "enabled", "update", "purpose")


def _progress_printer(name: str):
    def show(operation: dict[str, Any]) -> None:
        message = str(operation.get("message") or "")
        if ": " not in message:  # job-level lines; per-member steps already say "<member>: <step>"
            message = f"{name}: {message}"
        print(f"  [{int(operation.get('progress') or 0):3d}%] {message}")

    return show


def _print_table(rows: list[dict[str, Any]]) -> None:
    table = [
        (
            row["name"], row["kind"], row.get("version") or "-",
            row.get("installed_version") or "-", "yes" if row.get("enabled") else "no",
            "yes" if row.get("update_available") else "-",
            (row.get("purpose") or "")[:60],
        )
        for row in rows
    ]
    widths = [max(len(str(cell)) for cell in column) for column in zip(_LIST_COLUMNS, *table)]
    for line in (_LIST_COLUMNS, *table):
        print("  ".join(str(cell).ljust(width) for cell, width in zip(line, widths)).rstrip())


def _cmd_list(args: argparse.Namespace) -> int:
    payload = store.overview()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    _print_table(payload["verticals"])
    catalog = payload["catalog"]
    tag = catalog.get("release_tag") or "-"
    print(f"\ncatalog: {catalog['source']} (release {tag})")
    if catalog.get("error"):
        print(f"catalog error: {catalog['error']}", file=sys.stderr)
    if payload["host"]["managed_by_host"]:
        print(f"note: {store.HOST_MANAGED}")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    name = args.name.strip().lower()
    row = next((r for r in store.rows() if r["name"] == name), None)
    if row is None:
        print(f"argus: unknown vertical {args.name!r}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(row, ensure_ascii=False, indent=2))
        return 0
    for key in (
        "name", "kind", "purpose", "purpose_zh", "version", "installed_version", "enabled",
        "update_available", "requires", "shared", "python_requirements", "missing_python",
        "tags", "size_bytes", "used_by", "actions", "managed_by_host",
    ):
        value = row.get(key)
        rendered = ", ".join(map(str, value)) if isinstance(value, list) else value
        print(f"{key:20} {rendered if rendered not in (None, '', []) else '-'}")
    operation = row.get("operation")
    if operation:
        print(f"{'operation':20} {operation.get('action')} {operation.get('status')}: {operation.get('message', '')}")
    return 0


def _run_job(name: str, action) -> None:
    """Start the job, then follow its operation record until it ends."""
    operation = action(name, wait=False)
    print(f"{name}: {operation['action']} started")
    final = store.wait_for_operation(name, on_progress=_progress_printer(name))
    if final.get("status") != "done":
        raise store.VerticalStoreError(str(final.get("message") or f"{name}: {operation['action']} failed"))
    print(f"{name}: {final.get('message') or final.get('status')}")


def _cmd_install(args: argparse.Namespace) -> int:
    for name in args.names:
        _run_job(name, lambda n, **kw: store.install(n, **kw))
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    names = list(args.names)
    if not names:
        names = [
            row["name"] for row in store.rows()
            if row["kind"] == "installed" and row["update_available"]
        ]
        if not names:
            print("every installed vertical is current")
            return 0
    for name in names:
        _run_job(name, lambda n, **kw: store.update(n, **kw))
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    _run_job(args.name, lambda n, **kw: store.uninstall(n, force=args.force, **kw))
    return 0


def _cmd_toggle(args: argparse.Namespace, enabled: bool) -> int:
    entry = store.set_enabled(args.name, enabled)
    print(f"{args.name.strip().lower()}: {'enabled' if entry.get('enabled') else 'disabled'}")
    return 0


def _cmd_refresh(args: argparse.Namespace) -> int:
    loaded = store.load_catalog(refresh=True)
    catalog = loaded["catalog"]
    tag = catalog["release"].get("tag") or "-"
    print(f"catalog {loaded['source']}: {len(catalog['verticals'])} verticals (release {tag})")
    if loaded.get("error"):
        print(f"argus: {loaded['error']} (served the cached copy)", file=sys.stderr)
        return 1
    return 0


def run_verticals_command(args: argparse.Namespace) -> int:
    command = args.verticals_cmd
    try:
        if command == "list":
            return _cmd_list(args)
        if command == "info":
            return _cmd_info(args)
        if command == "install":
            return _cmd_install(args)
        if command == "update":
            return _cmd_update(args)
        if command == "remove":
            return _cmd_remove(args)
        if command == "enable":
            return _cmd_toggle(args, True)
        if command == "disable":
            return _cmd_toggle(args, False)
        if command == "refresh":
            return _cmd_refresh(args)
    except store.VerticalStoreError as exc:
        print(f"argus: {exc}", file=sys.stderr)
        return 1
    print(f"argus: unknown verticals command {command!r}", file=sys.stderr)
    return 2


__all__ = ["run_verticals_command"]
