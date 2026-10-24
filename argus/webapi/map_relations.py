"""What the map's cards are to each other, as the records state it.

A card's ``relations`` name the card it is about: ``{"kind", "target"}``. The
kinds are only the ones a record states, found without a model call:

- ``carries_out``: a team task the Manager dispatched for an operator's turn;
- ``from``: that task, when the turn quoted a card a person placed on the
  canvas (its "交给团队"): the task came from that card;
- ``about``: a turn that quoted a card (its "问这张卡"); or a Manager reply
  that links or names files a task delivered (or, when no task delivered
  them, a file a live task owns);
- ``checks`` / ``follows``: an item the Planner added right after another
  finished: a check when it works on the files that item delivered;
- ``replaces``: the first item of the plan that superseded an item.

The map draws these; time order is only its fallback for cards with none.
"""

from __future__ import annotations

from urllib.parse import unquote

from ..life.delivery import _referenced_path_candidates

PATH_LIMIT = 12
RELATION_LIMIT = 6


def reply_paths(text: str, workspace: str = "") -> list[str]:
    """Workspace-relative paths a reply links or names, as written."""
    paths: list[str] = []
    for candidate in _referenced_path_candidates(text):
        path = _normal(candidate, workspace)
        if path and path not in paths:
            paths.append(path)
        if len(paths) >= PATH_LIMIT:
            break
    return paths


def _normal(value: str, workspace: str = "") -> str:
    raw = unquote(str(value or "").strip()).split("#", 1)[0].split("?", 1)[0].strip("`'\"<>()[]")
    if not raw or "://" in raw or raw.startswith(("data:", "javascript:", "mailto:")):
        return ""
    if raw.startswith("file:"):
        raw = raw[len("file:"):].lstrip("/")
        raw = "/" + raw
    if workspace and raw.startswith(workspace.rstrip("/") + "/"):
        raw = raw[len(workspace.rstrip("/")) + 1:]
    raw = raw.removeprefix("./")
    return "" if raw.startswith("/") or ".." in raw.split("/") else raw


def relation_facts(rows: list[dict], facts: dict) -> dict:
    """Fold new event rows into what relations are made from; ``facts`` persists."""
    follows = facts.setdefault("follows", {})
    delivered = facts.setdefault("delivered", {})
    cycle = facts.setdefault("cycle", {"after": "", "added": []})
    for row in rows:
        kind, item = row.get("type"), str(row.get("item_id") or "")
        if kind == "life.planner.start":
            cycle.update(after="", added=[])
        elif kind == "life.planner.verdict" and row.get("mission_id"):
            # The Planner judged this item finished and planned what comes next.
            after = str(row["mission_id"])
            for added in cycle["added"]:
                follows.setdefault(added, after)
            cycle.update(after=after, added=[])
        elif kind == "life.planner.task_added" and item and not str(row.get("source") or "").startswith("manager"):
            if cycle["after"] and cycle["after"] != item:
                follows.setdefault(item, cycle["after"])
            else:
                cycle["added"].append(item)
        elif kind == "life.mission.completed" and item:
            paths = [_normal(path) for path in row.get("delivery_candidates") or [] if isinstance(path, str)]
            if paths := [path for path in paths if path]:
                delivered[item] = list(dict.fromkeys([*delivered.get(item, []), *paths]))[-PATH_LIMIT:]
    return facts


def project_relations(tasks: list[dict], turns: dict, facts: dict) -> list[dict]:
    """Set each task's ``relations`` from the records; returns ``tasks``."""
    by_id = {task["id"]: task for task in tasks}
    found: dict[str, list[dict]] = {task_id: [] for task_id in by_id}

    def add(source: str, kind: str, target: str):
        # A card a person placed ("hc-…") lives on the canvas, not in ``tasks``.
        if source == target or source not in found or (target not in by_id and not target.startswith("hc-")):
            return
        if not any(r["target"] == target for r in found[source]) and len(found[source]) < RELATION_LIMIT:
            found[source].append({"kind": kind, "target": target})

    delivered = facts.get("delivered") or {}
    superseded = {task["id"] for task in tasks if task.get("status") == "superseded"}
    # Replacement first: of two true links between one pair, it says the most.
    for task in tasks:
        if plan := task.get("superseded_by_plan_id"):
            later = [t for t in tasks if t["id"] != task["id"] and t.get("plan_id") == plan]
            if later:
                add(later[0]["id"], "replaces", task["id"])
    for item, after in (facts.get("follows") or {}).items():
        owned = set(by_id.get(item, {}).get("owns_paths") or [])
        add(item, "checks" if owned & set(delivered.get(after, [])) else "follows", after)
    for turn_id, turn in (turns or {}).items():
        refs = [ref for ref in turn.get("refs") or [] if isinstance(ref, str)]
        if dispatched := turn.get("dispatched"):
            for ref in refs:
                if ref.startswith("hc-"):
                    add(dispatched, "from", ref)
            add(dispatched, "carries_out", turn_id)
        for ref in refs:
            add(turn_id, "about", ref)
        paths = set(turn.get("paths") or [])
        if not paths or turn_id not in by_id:
            continue
        before = [t for t in tasks if t["id"] != turn_id and not str(t["id"]).startswith("turn:")
                  and (t.get("ts") or 0) <= (by_id[turn_id].get("finished_ts") or float("inf"))]
        delivering = [t for t in before if paths & set(delivered.get(t["id"], []))]
        owning = [t for t in before if paths & set(t.get("owns_paths") or []) and t["id"] not in superseded]
        targets = [t for t in delivering if t["id"] not in superseded] or delivering or owning
        for target in targets[:3]:
            add(turn_id, "about", target["id"])
    for task in tasks:
        task["relations"] = found.get(task["id"], [])
    return tasks
