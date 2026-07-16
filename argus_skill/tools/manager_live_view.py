"""Safe Manager-owned right-sidebar inspection and selection tool."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..core.event_catalog import normalize_event_envelope
from ..life.event_log import JsonlEventSink
from ..manager.live_view import (
    LiveViewDecision,
    apply_live_view_decision,
    load_live_view_decision,
    normalize_live_view_path,
)


def _resolved_file(workspace: Path, relative_path: str) -> Path | None:
    normalized = normalize_live_view_path(relative_path)
    if normalized is None:
        return None
    try:
        target = (workspace / normalized).resolve(strict=True)
        target.relative_to(workspace)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return None
    if not target.is_file() or target.is_symlink():
        return None
    return target


def _view_payload(
    workspace: Path,
    state_dir: Path,
) -> dict[str, Any]:
    view = load_live_view_decision(workspace, manifest_root=state_dir)
    rows = []
    if view is not None:
        for path in view.paths:
            target = _resolved_file(workspace, path)
            rows.append({
                "path": path,
                "exists": target is not None,
                "absolute_path": str(target) if target is not None else "",
            })
    return {
        "ok": True,
        "workspace": str(workspace),
        "state_dir": str(state_dir),
        "manifest": str(state_dir / ".argus" / "live-view.json"),
        "view": (
            {
                "title": view.title,
                "reason": view.reason,
                "paths": rows,
            }
            if view is not None
            else None
        ),
    }


def _emit(
    state_dir: Path,
    *,
    view: LiveViewDecision | None,
    action: str,
) -> None:
    event = normalize_event_envelope({
        "type": "manager.live_view.updated",
        "agent_layer": "manager",
        "title": view.title if view else "",
        "paths": list(view.paths) if view else [],
        "reason": view.reason if view else "",
        "explicit_clear": view is None,
        "source": "manager_tool",
        "text": (
            f"Manager {action} right sidebar: {view.title}"
            if view
            else "Manager cleared right sidebar"
        ),
    })
    JsonlEventSink(None, life_dir=state_dir, verbosity="full").append(event)


def set_view(
    *,
    workspace: Path,
    state_dir: Path,
    title: str,
    reason: str,
    paths: list[str],
) -> dict[str, Any]:
    normalized: list[str] = []
    missing: list[str] = []
    for raw in paths:
        path = normalize_live_view_path(raw)
        if path is None:
            missing.append(str(raw))
            continue
        if _resolved_file(workspace, path) is None:
            missing.append(path)
            continue
        if path not in normalized:
            normalized.append(path)
    if missing or not normalized:
        return {
            "ok": False,
            "error": "selected paths must be existing files in the canonical workspace",
            "workspace": str(workspace),
            "missing": missing,
        }
    view = LiveViewDecision(
        title=(title or "Live project view").strip()[:120],
        paths=tuple(normalized),
        reason=(reason or "").strip()[:500],
    )
    apply_live_view_decision(
        workspace,
        decided=True,
        view=view,
        manifest_root=state_dir,
    )
    _emit(state_dir, view=view, action="updated")
    return _view_payload(workspace, state_dir)


def clear_view(*, workspace: Path, state_dir: Path) -> dict[str, Any]:
    apply_live_view_decision(
        workspace,
        decided=True,
        view=None,
        manifest_root=state_dir,
    )
    _emit(state_dir, view=None, action="cleared")
    return _view_payload(workspace, state_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m argus_skill.tools.manager_live_view"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--state-dir", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    set_parser = sub.add_parser("set")
    set_parser.add_argument("--title", required=True)
    set_parser.add_argument("--reason", default="")
    set_parser.add_argument("--path", action="append", required=True)
    sub.add_parser("clear")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).expanduser().resolve()
    state_dir = Path(args.state_dir).expanduser().resolve()
    if not workspace.is_dir() or not state_dir.is_dir():
        sys.stderr.write("manager_live_view: workspace and state-dir must exist\n")
        return 2
    if args.command == "status":
        payload = _view_payload(workspace, state_dir)
    elif args.command == "set":
        payload = set_view(
            workspace=workspace,
            state_dir=state_dir,
            title=args.title,
            reason=args.reason,
            paths=args.path,
        )
    else:
        payload = clear_view(workspace=workspace, state_dir=state_dir)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return 0 if payload.get("ok") else 2


__all__ = ["clear_view", "main", "set_view"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
