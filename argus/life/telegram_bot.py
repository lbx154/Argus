"""Telegram Bot poller — inbound command interface for the daemon.

Runs as a daemon thread inside :class:`~argus_skill.daemon.life_worker.LifeWorker`.
Polls ``getUpdates`` with long-polling and dispatches the shared operator
commands defined in :mod:`argus_skill.life.chat.router` (``/add``, ``/status``,
``/nudge`` …). Only messages from the configured ``chat_id`` (and optionally
``user_id``) are processed. Everything else is silently dropped.

Phone-facing behaviour lives here rather than in the router:

* Replies longer than Telegram's 4096-character cap are **split**, not
  truncated — a long ``/journal`` used to lose its tail.
* The command list is published via ``setMyCommands`` on startup, so the
  Telegram client shows the whole surface in its ``/`` menu instead of making
  the operator remember it.
* ``/status`` carries an inline keyboard, so the common follow-ups are one
  thumb-tap away instead of a typed command.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from ..apps._life_actions import DEFAULT_LIFE_CONFIG
from .chat.render import TELEGRAM_LIMIT, chunk_html
from .chat.router import COMMAND_MENU, CommandRouter, _esc, _fmt_duration, help_text
from .chat.transport import ChatTransport

log = logging.getLogger(__name__)

# ``_esc`` / ``_fmt_duration`` are re-exported for callers that imported them
# from this module before the command surface moved into ``.chat``.
__all__ = [
    "TelegramPoller",
    "TelegramTransport",
    "_CommandRouter",
    "_esc",
    "_fmt_duration",
    "publish_command_menu",
    "telegram_enabled",
]

#: Kept as a module attribute for callers that imported it before the command
#: surface moved to :mod:`argus_skill.life.chat.router`.
_HELP_TEXT = help_text("Telegram")


def telegram_enabled() -> bool:
    """Whether the optional Telegram command interface is explicitly enabled."""
    return (os.environ.get("ARGUS_SKILL_ENABLE_TELEGRAM") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

def _api_call(token: str, method: str, payload: dict[str, Any] | None = None, *, timeout: float = 35) -> dict[str, Any] | None:
    """Call a Telegram Bot API method. Returns the parsed JSON or None on error."""
    import urllib.request
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    try:
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("telegram api %s failed: %s", method, exc)
        return None


#: Quick actions attached to ``/status``. Callback payloads are the commands
#: themselves, which keeps them well inside Telegram's 64-byte limit and means
#: a tap and a typed command take the exact same path through the router.
_STATUS_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "🔄 刷新状态", "callback_data": "/status"},
            {"text": "📋 待办", "callback_data": "/backlog"},
        ],
        [
            {"text": "📓 日志", "callback_data": "/journal"},
            {"text": "❓ 帮助", "callback_data": "/help"},
        ],
    ]
}


def _send_message(
    token: str,
    chat_id: str,
    text: str,
    *,
    parse_mode: str = "HTML",
    reply_markup: dict[str, Any] | None = None,
) -> None:
    """Send a reply to the configured chat, splitting it if it exceeds the cap.

    The keyboard, when present, rides on the final chunk so it sits at the
    bottom of the conversation where a thumb reaches it.
    """
    chunks = chunk_html(text, TELEGRAM_LIMIT)
    for index, chunk in enumerate(chunks):
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None and index == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        _api_call(token, "sendMessage", payload, timeout=10)


def publish_command_menu(token: str) -> bool:
    """Register the command list so Telegram clients show a ``/`` menu.

    Best-effort: a failure here costs discoverability, never functionality.
    """
    commands = [
        {"command": name, "description": description[:256]}
        for name, description in COMMAND_MENU
    ]
    resp = _api_call(token, "setMyCommands", {"commands": commands}, timeout=10)
    ok = bool(resp and resp.get("ok"))
    if not ok:
        log.debug("telegram: setMyCommands did not take effect")
    return ok


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

class TelegramTransport(ChatTransport):
    """Outbound half of the Telegram bridge."""

    channel = "telegram"
    display_name = "Telegram"

    def __init__(self, *, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id

    def send(self, text: str) -> None:
        # The status card is the natural hub, so that is where the quick
        # actions go; every other reply stays clean.
        markup = _STATUS_KEYBOARD if text.startswith("📊") else None
        _send_message(self.token, self.chat_id, text, reply_markup=markup)


# ---------------------------------------------------------------------------
# Command router
# ---------------------------------------------------------------------------

class _CommandRouter(CommandRouter):
    """Telegram-bound router.

    The command bodies now live in :class:`argus_skill.life.chat.router.CommandRouter`;
    this subclass only keeps the historical ``(life_dir, token, chat_id)``
    construction used by the poller and existing callers.
    """

    def __init__(self, *, life_dir: Path, token: str, chat_id: str) -> None:
        super().__init__(
            life_dir=life_dir,
            transport=TelegramTransport(token=token, chat_id=chat_id),
        )
        self.token = token
        self.chat_id = chat_id


# ---------------------------------------------------------------------------
# Offset persistence
# ---------------------------------------------------------------------------

def _offset_path(life_dir: Path) -> Path:
    return life_dir / "telegram.offset"


def _read_offset(life_dir: Path) -> int | None:
    p = _offset_path(life_dir)
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return None


def _write_offset(life_dir: Path, offset: int) -> bool:
    path = _offset_path(life_dir)
    tmp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        tmp = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(offset))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        log.warning("failed to persist telegram offset")
        return False
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _fast_forward(token: str, life_dir: Path) -> int | None:
    """Skip all pending updates and return the next offset."""
    resp = _api_call(token, "getUpdates", {"offset": -1, "limit": 1, "timeout": 0}, timeout=10)
    if resp and resp.get("ok") and resp.get("result"):
        offset = resp["result"][-1]["update_id"] + 1
        return offset if _write_offset(life_dir, offset) else None
    return 0


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------


class TelegramPoller:
    """Long-polling thread that listens for inbound Telegram commands.

    Start with :meth:`start` (spawns a daemon thread). Stops when the
    ``stop_event`` fires or the parent process exits.
    """

    def __init__(
        self,
        *,
        life_dir: Path,
        token: str | None = None,
        chat_id: str | None = None,
        user_id: str | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.life_dir = life_dir
        self.token = (token or os.environ.get("ARGUS_SKILL_TELEGRAM_BOT_TOKEN") or "").strip()
        self.chat_id = (chat_id or os.environ.get("ARGUS_SKILL_TELEGRAM_CHAT_ID") or "").strip()
        self.user_id = (user_id or os.environ.get("ARGUS_SKILL_TELEGRAM_USER_ID") or "").strip()
        self._stop = stop_event or threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {
            "backend": os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex"),
            "config": dict(DEFAULT_LIFE_CONFIG),
            "continuous_objective": "",
            "last_thread_id": None,
        }

    @property
    def enabled(self) -> bool:
        return telegram_enabled() and bool(self.token and self.chat_id)

    def _sender_allowed(self, sender: Any) -> bool:
        if not self.user_id:
            return True
        if not isinstance(sender, dict):
            return False
        return str(sender.get("id", "")) == self.user_id

    def _message_allowed(self, msg: dict[str, Any]) -> bool:
        chat = msg.get("chat")
        if not isinstance(chat, dict):
            return False
        msg_chat_id = str(chat.get("id", ""))
        if msg_chat_id != self.chat_id:
            return False
        return self._sender_allowed(msg.get("from"))

    def start(self) -> None:
        if not self.enabled:
            log.info("telegram poller disabled (missing token or chat_id)")
            return
        self._thread = threading.Thread(
            target=self._poll_loop, name="telegram-poller", daemon=True
        )
        self._thread.start()
        log.info("telegram poller started")

    # -- main loop ---------------------------------------------------------

    def _handle_callback(self, query: dict[str, Any], router: _CommandRouter) -> None:
        """Run an inline-keyboard tap through the same router as typed text."""
        # Always acknowledge, or the client shows a spinner until it times out.
        _api_call(
            self.token,
            "answerCallbackQuery",
            {"callback_query_id": str(query.get("id", ""))},
            timeout=10,
        )
        message = query.get("message")
        if not isinstance(message, dict) or not self._message_allowed(message):
            log.debug("telegram: ignoring unauthorized callback")
            return
        if not self._sender_allowed(query.get("from")):
            log.debug("telegram: ignoring callback from unauthorized sender")
            return
        data = str(query.get("data") or "").strip()
        if data.startswith("/"):
            log.info("telegram callback: %s", data[:80])
            router.dispatch(data)

    def _poll_loop(self) -> None:
        router = _CommandRouter(
            life_dir=self.life_dir, token=self.token, chat_id=self.chat_id,
        )
        publish_command_menu(self.token)

        # Recover offset or fast-forward to skip stale messages
        offset = _read_offset(self.life_dir)
        if offset is None:
            log.info("telegram poller: first boot, fast-forwarding updates")
            offset = _fast_forward(self.token, self.life_dir)
            if offset is None:
                log.error("telegram poller: cannot persist initial offset")
                return

        backoff = 1.0

        while not self._stop.is_set():
            try:
                resp = _api_call(self.token, "getUpdates", {
                    "offset": offset,
                    "limit": 20,
                    "timeout": 30,
                    "allowed_updates": ["message", "callback_query"],
                }, timeout=35)

                if not resp or not resp.get("ok"):
                    self._stop.wait(timeout=min(backoff, 30))
                    backoff = min(backoff * 2, 60)
                    continue

                backoff = 1.0
                updates = resp.get("result") or []

                for update in updates:
                    uid = update.get("update_id", 0)
                    next_offset = uid + 1
                    if not _write_offset(self.life_dir, next_offset):
                        self._stop.wait(timeout=min(backoff, 30))
                        backoff = min(backoff * 2, 60)
                        break
                    offset = next_offset

                    callback = update.get("callback_query")
                    if isinstance(callback, dict):
                        self._handle_callback(callback, router)
                        continue

                    msg = update.get("message") or {}
                    text = (msg.get("text") or "").strip()

                    if not self._message_allowed(msg):
                        log.debug("telegram: ignoring unauthorized message")
                        continue
                    if not text:
                        continue

                    log.info("telegram command: %s", text[:80])
                    router.dispatch(text)

            except Exception:  # noqa: BLE001
                log.exception("telegram poller error; retrying in %.0fs", backoff)
                self._stop.wait(timeout=min(backoff, 60))
                backoff = min(backoff * 2, 60)
