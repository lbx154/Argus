"""What the map's cards are to each other, as the records state it.

A card's ``relations`` name the card it is about: ``{"kind", "target"}``. The
kinds are only the ones a record states, found without a model call:

- ``carries_out``: a team task the Manager dispatched for an operator's turn;
- ``from``: that task, when the turn quoted a card a person placed on the
  canvas (its "交给团队"): the task came from that card;
- ``about``: a turn that quoted a card (its "问这张卡"); or a Manager reply
  that links files a task delivered or writes them as code (or, when no task
  delivered them, a file a live task owns);
- ``checks`` / ``follows``: an item the Planner added right after another
  finished: a check when it works on the files that item delivered;
- ``replaces``: the first item of the plan that superseded an item.

The map draws these; time order is only its fallback for cards with none.

The map shows the work, not the chat (design/role-authority.md "Map: one
picture of the work"). ``fold_cards`` turns what a person said into a small
request on the task it started, leaves chat-only answers off the map, and
folds a replaced task into the task that replaced it.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

PATH_LIMIT = 12
RELATION_LIMIT = 6
_LINK = re.compile(r"!?\[[^\]\r\n]*\]\(\s*(?:<([^>\r\n]+)>|([^\s)\r\n]+))")
_CODE = re.compile(r"(?<!`)`([^`\r\n]{1,2048})`(?!`)")


def reply_paths(text: str, workspace: str = "") -> list[str]:
    """Workspace-relative paths a reply links or writes as code, as written."""
    paths: list[str] = []
    links = [angled or plain for angled, plain in _LINK.findall(text)]
    for candidate in [*links, *_CODE.findall(text)]:
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


REQUEST_TEXT_LIMIT = 600
LOOSE_REQUEST_LIMIT = 5
REPLACED_LIMIT = 12


def _request(turn: dict) -> dict:
    """A person's request as the small chip on the map: their words, short and in full."""
    request = {
        "id": turn["id"],
        "text": str(turn.get("title") or "")[:120],
        "full": str(turn.get("objective") or turn.get("title") or "")[:REQUEST_TEXT_LIMIT],
        "ts": turn.get("ts") or 0,
        "status": str(turn.get("status") or ""),
    }
    if turn.get("summary"):
        request["reply"] = str(turn["summary"])[:200]
    return request


def fold_cards(tasks: list[dict]) -> tuple[list[dict], list[dict]]:
    """One picture of the work; returns the cards that stay and the requests
    that started no task yet.

    - A request (a turn card) is not a card. When a task carries it out, or
      it answered a task's question ("继续"), it rides on that task as
      ``requests``. A request still being read is returned loose; anything
      else was chat (a ``qa`` answer, small work done in the conversation)
      and stays in the conversation.
    - A task a newer plan replaced before it finished (``superseded``) folds
      into the task that replaced it: that task lists it under ``replaced``
      (title and one-line reason), takes over its requests and relations, and
      lines and dependencies that pointed at it point at the replacement.
    """
    by_id = {task["id"]: task for task in tasks}
    into: dict[str, str] = {}
    for task in tasks:
        for relation in task.get("relations") or ():
            old = by_id.get(relation["target"])
            if relation["kind"] == "replaces" and old and old.get("status") == "superseded":
                into.setdefault(old["id"], task["id"])

    def final(task_id: str) -> str:
        seen = {task_id}
        while task_id in into and into[task_id] not in seen:
            task_id = into[task_id]
            seen.add(task_id)
        return task_id

    turns = {task["id"] for task in tasks if task.get("kind") == "turn"}
    carried: dict[str, str] = {}
    for task in sorted(tasks, key=lambda t: (t.get("ts") or 0, t["id"])):
        for relation in task.get("relations") or ():
            if relation["kind"] == "carries_out" and relation["target"] in turns:
                carried.setdefault(relation["target"], task["id"])
    gone = turns | {task_id for task_id in into if final(task_id) != task_id}
    kept = [task for task in tasks if task["id"] not in gone]
    requests: dict[str, list[dict]] = {}
    loose = []
    for task in tasks:
        if task["id"] not in turns or task.get("turn_kind") == "qa":
            continue
        carrier = carried.get(task["id"]) or task.get("answers")
        if carrier in by_id and carrier not in turns:
            requests.setdefault(final(carrier), []).append(_request(task))
        elif task.get("status") == "running":
            # Still being read: it may yet start a task.
            loose.append(_request(task))
    replaced: dict[str, list[dict]] = {}
    for task in tasks:
        if task["id"] in gone and task["id"] not in turns:
            reason = " ".join(str(task.get("superseded_reason") or "").split())
            replaced.setdefault(final(task["id"]), []).append({
                "id": task["id"], "title": str(task.get("title") or "")[:160],
                "reason": reason[:160], "ts": task.get("ts") or 0,
            })
    for task in kept:
        own = task["id"]
        relations, seen = [], set()
        merged = [*(task.get("relations") or ()), *(
            relation for old in tasks if old["id"] in into and final(old["id"]) == own
            for relation in old.get("relations") or ()
        )]
        for relation in merged:
            target = relation["target"]
            if target in turns:
                continue
            target = final(target)
            if target == own or target in seen or (target not in by_id and not target.startswith("hc-")):
                continue
            seen.add(target)
            relations.append({**relation, "target": target})
        task["relations"] = relations[:RELATION_LIMIT]
        if task.get("deps"):
            task["deps"] = [dep for dep in dict.fromkeys(final(dep) for dep in task["deps"]) if dep != own]
        if own in requests:
            task["requests"] = sorted(requests[own], key=lambda r: r["ts"])
        if own in replaced:
            task["replaced"] = sorted(replaced[own], key=lambda r: r["ts"])[-REPLACED_LIMIT:]
            task["replaced_count"] = len(replaced[own])
    loose.sort(key=lambda r: r["ts"])
    return kept, loose[-LOOSE_REQUEST_LIMIT:]
