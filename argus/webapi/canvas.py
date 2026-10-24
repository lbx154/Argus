"""The project canvas: what a person placed on the map beside Argus's cards.

One JSON record per project, ``<life_dir>/canvas.json``::

    {"version": 1, "revision": 7,
     "cards": [{"id": "hc-…", "kind": "idea" | "file", "text": "…",
                "attachment": {...} | None, "created_ts": …, "updated_ts": …}],
     "links": [{"id": "hl-…", "source": "<card id>", "target": "<card id>",
                "kind": "basis" | "order" | "check" | "related", "created_ts": …}],
     "positions": {"<card id>": {"x": …, "y": …}}}

``cards`` and ``links`` hold only what a person made. ``positions`` holds every
card a person moved, human or Argus: a task card with a position here is never
laid out again (experience rule 5). Links may join any two cards, so an end is
either a human card id or an Argus map card id.

Every write names the revision it was based on; a stale revision raises
:class:`CanvasConflict` so two browsers never overwrite each other. Nothing here
calls a model.

Hook for the Manager (canvas sub-step 2): :func:`human_canvas_digest` returns the
short, capped list of what a person placed, ready to be shown as context.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

import portalocker

CANVAS_FILENAME = "canvas.json"
CANVAS_VERSION = 1
CARD_KINDS = ("idea", "file")
LINK_KINDS = ("basis", "order", "check", "related")
MAX_CARDS = 300
MAX_LINKS = 600
MAX_POSITIONS = 3000
MAX_TEXT_CHARS = 2000
MAX_ID_CHARS = 160
MAX_COORD = 10_000_000.0
_ATTACHMENT_FIELDS = {"attachment_id": 128, "name": 512, "relative_path": 1024, "mime": 128}

_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()


class CanvasError(ValueError):
    """A request the canvas cannot accept; ``status`` is the HTTP status."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


class CanvasConflict(CanvasError):
    def __init__(self, current: dict[str, Any]):
        super().__init__("the canvas changed in another window; reload and retry", 409)
        self.current = current


def canvas_path(life_dir: Path | str) -> Path:
    return Path(life_dir) / CANVAS_FILENAME


def empty_canvas() -> dict[str, Any]:
    return {"version": CANVAS_VERSION, "revision": 0, "cards": [], "links": [], "positions": {}}


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    lock_path = path.parent / f"{path.name}.lock"
    key = os.path.normcase(str(lock_path))
    with _LOCKS_GUARD:
        thread_lock = _LOCKS.setdefault(key, threading.Lock())
    with thread_lock, lock_path.open("a+b") as handle:
        portalocker.lock(handle, portalocker.LOCK_EX)
        try:
            yield
        finally:
            portalocker.unlock(handle)


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_canvas()
    except (OSError, ValueError):
        # A damaged record must not take the map down; the next write replaces it.
        return empty_canvas()
    canvas = empty_canvas()
    if isinstance(raw, dict):
        canvas["revision"] = int(raw.get("revision") or 0)
        canvas["cards"] = [c for c in raw.get("cards") or [] if isinstance(c, dict) and c.get("id")]
        canvas["links"] = [link for link in raw.get("links") or [] if isinstance(link, dict) and link.get("id")]
        positions = raw.get("positions")
        canvas["positions"] = positions if isinstance(positions, dict) else {}
    return canvas


def _write(path: Path, canvas: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(canvas, handle, ensure_ascii=False, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_canvas(life_dir: Path | str) -> dict[str, Any]:
    return _read(canvas_path(life_dir))


def _mutate(life_dir: Path | str, revision: int, change: Callable[[dict[str, Any]], Any]) -> tuple[dict[str, Any], Any]:
    path = canvas_path(life_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _locked(path):
        canvas = _read(path)
        if revision != canvas["revision"]:
            raise CanvasConflict(canvas)
        result = change(canvas)
        canvas["revision"] += 1
        _write(path, canvas)
    return canvas, result


def _text(value: Any, *, required: bool) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise CanvasError("card text is empty")
    if len(text) > MAX_TEXT_CHARS:
        raise CanvasError(f"card text is longer than {MAX_TEXT_CHARS} characters")
    return text


def _card_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > MAX_ID_CHARS or any(ch in text for ch in "\n\r\t"):
        raise CanvasError("card id is empty or too long")
    return text


def _point(value: Any) -> dict[str, float] | None:
    if value is None:
        return None
    try:
        x, y = float(value["x"]), float(value["y"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CanvasError("a position needs numeric x and y") from exc
    if not (abs(x) <= MAX_COORD and abs(y) <= MAX_COORD):
        raise CanvasError("a position is out of range")
    return {"x": round(x, 2), "y": round(y, 2)}


def _attachment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CanvasError("a file card needs its uploaded attachment")
    out: dict[str, Any] = {}
    for field, limit in _ATTACHMENT_FIELDS.items():
        text = str(value.get(field) or "").strip()
        if len(text) > limit:
            raise CanvasError(f"attachment {field} is too long")
        out[field] = text
    if not out["attachment_id"] or not out["name"]:
        raise CanvasError("a file card needs the attachment id and file name")
    try:
        out["size_bytes"] = max(0, int(value.get("size_bytes") or 0))
    except (TypeError, ValueError) as exc:
        raise CanvasError("attachment size must be a number") from exc
    return out


def _human_card(canvas: dict[str, Any], card_id: str) -> dict[str, Any]:
    for card in canvas["cards"]:
        if card["id"] == card_id:
            return card
    # Argus task cards are not stored here and can never be deleted or edited.
    raise CanvasError("no card a person added has this id", 404)


def add_card(
    life_dir: Path | str, revision: int, *, kind: str, text: str = "",
    attachment: dict[str, Any] | None = None, position: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if kind not in CARD_KINDS:
        raise CanvasError(f"card kind must be one of {', '.join(CARD_KINDS)}")
    clean_text = _text(text, required=kind == "idea")
    clean_attachment = _attachment(attachment) if kind == "file" else None
    point = _point(position)

    def change(canvas: dict[str, Any]) -> dict[str, Any]:
        if len(canvas["cards"]) >= MAX_CARDS:
            raise CanvasError(f"the canvas already holds {MAX_CARDS} cards; remove some first", 413)
        now = time.time()
        card = {"id": f"hc-{uuid.uuid4().hex[:12]}", "kind": kind, "text": clean_text,
                "attachment": clean_attachment, "created_ts": now, "updated_ts": now}
        canvas["cards"].append(card)
        if point:
            canvas["positions"][card["id"]] = point
        return card

    return _mutate(life_dir, revision, change)


def update_card(life_dir: Path | str, revision: int, card_id: str, *, text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    def change(canvas: dict[str, Any]) -> dict[str, Any]:
        card = _human_card(canvas, card_id)
        card["text"] = _text(text, required=card["kind"] == "idea")
        card["updated_ts"] = time.time()
        return card

    return _mutate(life_dir, revision, change)


def delete_card(life_dir: Path | str, revision: int, card_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    def change(canvas: dict[str, Any]) -> dict[str, Any]:
        card = _human_card(canvas, card_id)
        canvas["cards"] = [c for c in canvas["cards"] if c["id"] != card_id]
        canvas["links"] = [link for link in canvas["links"] if card_id not in (link["source"], link["target"])]
        canvas["positions"].pop(card_id, None)
        return card

    return _mutate(life_dir, revision, change)


def add_link(life_dir: Path | str, revision: int, *, source: str, target: str, kind: str) -> tuple[dict[str, Any], dict[str, Any]]:
    source, target = _card_id(source), _card_id(target)
    if source == target:
        raise CanvasError("a link joins two different cards")
    if kind not in LINK_KINDS:
        raise CanvasError(f"link kind must be one of {', '.join(LINK_KINDS)}")

    def change(canvas: dict[str, Any]) -> dict[str, Any]:
        human = {c["id"] for c in canvas["cards"]}
        for end in (source, target):
            if end.startswith("hc-") and end not in human:
                raise CanvasError("a link end names a card that was removed", 404)
        for link in canvas["links"]:
            if {link["source"], link["target"]} == {source, target} and link["kind"] == kind:
                return link
        if len(canvas["links"]) >= MAX_LINKS:
            raise CanvasError(f"the canvas already holds {MAX_LINKS} links; remove some first", 413)
        link = {"id": f"hl-{uuid.uuid4().hex[:12]}", "source": source, "target": target,
                "kind": kind, "created_ts": time.time()}
        canvas["links"].append(link)
        return link

    return _mutate(life_dir, revision, change)


def update_link(life_dir: Path | str, revision: int, link_id: str, *, kind: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if kind not in LINK_KINDS:
        raise CanvasError(f"link kind must be one of {', '.join(LINK_KINDS)}")

    def change(canvas: dict[str, Any]) -> dict[str, Any]:
        for link in canvas["links"]:
            if link["id"] == link_id:
                link["kind"] = kind
                return link
        raise CanvasError("no link a person drew has this id", 404)

    return _mutate(life_dir, revision, change)


def delete_link(life_dir: Path | str, revision: int, link_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    def change(canvas: dict[str, Any]) -> dict[str, Any]:
        for link in canvas["links"]:
            if link["id"] == link_id:
                canvas["links"].remove(link)
                return link
        raise CanvasError("no link a person drew has this id", 404)

    return _mutate(life_dir, revision, change)


def set_positions(life_dir: Path | str, revision: int, positions: dict[str, Any]) -> tuple[dict[str, Any], None]:
    """Merge moved cards' places; ``None`` for a card forgets its place."""
    if len(positions) > MAX_POSITIONS:
        raise CanvasError("too many positions in one request", 413)
    clean = {_card_id(key): _point(value) for key, value in positions.items()}

    def change(canvas: dict[str, Any]) -> None:
        for key, point in clean.items():
            if point is None:
                canvas["positions"].pop(key, None)
            else:
                canvas["positions"][key] = point
        if len(canvas["positions"]) > MAX_POSITIONS:
            raise CanvasError(f"the canvas remembers at most {MAX_POSITIONS} card places", 413)

    return _mutate(life_dir, revision, change)


def human_canvas_digest(canvas: dict[str, Any], *, limit: int = 30) -> list[str]:
    """Short lines describing what a person placed, newest and linked first.

    Not wired yet: the Manager reads this in canvas sub-step 2.
    """
    linked = {end for link in canvas.get("links") or [] for end in (link["source"], link["target"])}
    cards = sorted(canvas.get("cards") or [], key=lambda c: (c["id"] in linked, c.get("updated_ts") or 0), reverse=True)
    lines = []
    for card in cards[:limit]:
        if card["kind"] == "file":
            lines.append(f"{card['id']} 资料卡: {(card.get('attachment') or {}).get('relative_path') or ''}")
        else:
            lines.append(f"{card['id']} 想法卡: {card['text'][:300]}")
    for link in (canvas.get("links") or [])[:limit]:
        lines.append(f"{link['source']} -[{link['kind']}]-> {link['target']}")
    return lines
