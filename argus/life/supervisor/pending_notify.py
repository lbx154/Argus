"""Tell the operator when the daemon is waiting on them.

A blocked reviewer verdict carrying an operator question parks the mission and
writes ``pending_question`` to disk. The portal shows it — to whoever is
looking at the portal. Nobody watches a long-running research daemon, so the
common outcome is a run that stops for a decision and then simply sits there,
sometimes for hours, while the operator assumes it is still working.

The chat bridges already reach the operator's phone. This sends the question
there the moment it is recorded, so the wait is bounded by how long it takes
someone to read a message rather than by when they next open the portal.

Notification is best-effort by construction: a channel that is off,
misconfigured, or briefly unreachable must never affect the mission that just
paused. Every failure is logged and swallowed.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "notify_pending_question",
    "pending_question_message",
    "should_report_pending_wait",
]

#: Ids already announced, so a restart or a re-read does not re-notify.
_SENT_RELPATH = "pending_notified.json"
_WAIT_RELPATH = "pending_wait_state.json"


def _sent_path(life_dir: Path) -> Path:
    return Path(life_dir) / _SENT_RELPATH


def _load_sent(life_dir: Path) -> dict[str, str]:
    try:
        payload = json.loads(_sent_path(life_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in payload.items()} if isinstance(payload, dict) else {}


def _record_sent(life_dir: Path, item_id: str, question: str) -> None:
    """Remember that *item_id*'s current question was announced.

    Keyed by question text as well as id: when a mission pauses again with a
    different question, that is new information and should be sent.
    """
    sent = _load_sent(life_dir)
    sent[item_id] = question
    path = _sent_path(life_dir)
    tmp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        tmp = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(sent, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        tmp = None
    except OSError:
        # Losing the ledger costs a duplicate message, never a missed one.
        log.debug("pending-question notify: could not persist the sent ledger")
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def should_report_pending_wait(
    life_dir: Path | str,
    items: Any,
    *,
    heartbeat_seconds: float = 3600.0,
    now: float | None = None,
) -> bool:
    """Persistently deduplicate the daemon's "waiting for you" heartbeat.

    Daemon outer loops may create fresh supervisor objects, so in-memory log
    suppression is insufficient.  Report immediately when the set or text of
    pending questions changes, then at most once per heartbeat interval.
    """
    directory = Path(life_dir)
    rows = sorted(
        (
            str(getattr(item, "id", "") or ""),
            str(getattr(item, "pending_question", "") or "").strip(),
        )
        for item in (items or [])
        if str(getattr(item, "pending_question", "") or "").strip()
    )
    if not rows:
        return False
    signature = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    path = directory / _WAIT_RELPATH
    try:
        prior = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prior = {}
    timestamp = float(time.time() if now is None else now)
    try:
        last_at = float(prior.get("reported_at") or 0.0)
    except (TypeError, ValueError):
        last_at = 0.0
    if (
        str(prior.get("signature") or "") == signature
        and timestamp - last_at < max(0.0, float(heartbeat_seconds))
    ):
        return False

    tmp: Path | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            dir=directory,
            prefix=f".{_WAIT_RELPATH}.",
            suffix=".tmp",
        )
        tmp = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {"signature": signature, "reported_at": timestamp},
                handle,
                ensure_ascii=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        tmp = None
    except OSError:
        # If state cannot be persisted, prefer one duplicate status over
        # suppressing the only visible explanation for an idle daemon.
        log.debug("pending-question wait: could not persist dedup state")
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    return True


def pending_question_message(
    *, project: str, title: str, question: str, options: Any = None
) -> str:
    """The message an operator should be able to act on from a phone."""
    from ...core.operator_messages import uses_cjk

    chinese = uses_cjk(question)
    lines = [
        "🟡 <b>需要你决定</b>" if chinese else "🟡 <b>Your decision is needed</b>",
        "",
        f"项目：{project}" if chinese else f"Project: {project}",
        f"任务：{title}" if chinese else f"Task: {title}",
        "",
        question.strip(),
    ]
    choices = [
        str(option.get("label") or option.get("id") or "").strip()
        for option in (options or [])
        if isinstance(option, dict)
    ]
    choices = [choice for choice in choices if choice]
    if choices:
        lines += [
            "",
            ("可选：" if chinese else "Options: ") + " / ".join(choices[:6]),
        ]
    lines += [
        "",
        "直接回复，或在网页端处理。"
        if chinese
        else "Reply here or answer in the web app.",
    ]
    return "\n".join(lines)


def notify_pending_question(life_dir: Any, item: Any) -> bool:
    """Announce *item*'s operator question on every enabled chat channel.

    Returns whether anything was sent. Never raises: the mission has already
    paused correctly, and a notification problem must not change that.
    """
    try:
        directory = Path(str(life_dir))
        question = str(getattr(item, "pending_question", "") or "").strip()
        item_id = str(getattr(item, "id", "") or "")
        if not question or not item_id:
            return False
        if _load_sent(directory).get(item_id) == question:
            return False

        decision = getattr(item, "operator_decision", None)
        message = pending_question_message(
            project=directory.name,
            title=str(getattr(item, "title", "") or item_id)[:80],
            question=question,
            options=(decision or {}).get("options") if isinstance(decision, dict) else None,
        )

        sent = False
        for send in (_send_telegram, _send_feishu):
            try:
                sent = send(message) or sent
            except Exception:  # noqa: BLE001 - a channel must not break the pause
                log.exception("pending-question notify: channel failed")
        if sent:
            _record_sent(directory, item_id, question)
        return sent
    except Exception:  # noqa: BLE001
        log.exception("pending-question notify: unexpected failure")
        return False


def _send_telegram(message: str) -> bool:
    from ..telegram_bot import TelegramTransport, telegram_enabled

    if not telegram_enabled():
        return False
    token = (os.environ.get("ARGUS_SKILL_TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("ARGUS_SKILL_TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return False
    TelegramTransport(token=token, chat_id=chat_id).send(message)
    return True


def _send_feishu(message: str) -> bool:
    from ..feishu_bot import FeishuTransport, feishu_enabled

    if not feishu_enabled():
        return False
    app_id = (os.environ.get("ARGUS_SKILL_FEISHU_APP_ID") or "").strip()
    secret = (os.environ.get("ARGUS_SKILL_FEISHU_APP_SECRET") or "").strip()
    chat_id = (os.environ.get("ARGUS_SKILL_FEISHU_CHAT_ID") or "").strip()
    if not (app_id and secret and chat_id):
        return False
    FeishuTransport(app_id=app_id, app_secret=secret, chat_id=chat_id).send(message)
    return True
