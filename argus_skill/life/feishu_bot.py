"""Feishu / Lark bot — inbound command interface over a WebSocket long connection.

Runs as a daemon thread inside :class:`~argus_skill.daemon.life_worker.LifeWorker`,
alongside the Telegram poller, and serves the same operator commands from
:mod:`argus_skill.life.chat.router`.

**Why a long connection.** Feishu's usual integration is an event-subscription
webhook: you publish an HTTPS endpoint and Feishu POSTs to it. A daemon running
on a workstation or a cluster node behind NAT has no such endpoint, and asking
operators to stand up a tunnel to read ``/status`` from their phone is a poor
trade. Feishu's long-connection mode inverts the direction — the daemon dials
out and events arrive on the socket — so there is no inbound port, no public
URL, and no callback address to configure. It is the same shape as Telegram's
long polling.

**Setup.**

1. Create an app at https://open.feishu.cn/ (or https://open.larksuite.com/ for
   international Lark) and add the *bot* capability.
2. Grant the ``im:message`` and ``im:message:send_as_bot`` scopes. Reactions
   additionally need ``im:message.reaction``.
3. Under *Event Subscription*, choose **长连接 / long connection** — not a
   request URL — and subscribe to ``im.message.receive_v1``.
4. Export the credentials and start the daemon::

       export ARGUS_SKILL_ENABLE_FEISHU=1
       export ARGUS_SKILL_FEISHU_APP_ID=cli_xxx
       export ARGUS_SKILL_FEISHU_APP_SECRET=xxx
       # optional: restrict who may drive the daemon
       export ARGUS_SKILL_FEISHU_ALLOWED_USERS=ou_xxx,ou_yyy

5. ``pip install 'argus-skill[feishu]'`` for the ``lark-oapi`` SDK. Without it
   the bridge logs one line and stays dormant; nothing else is affected.

Outbound calls go through :mod:`urllib` rather than the SDK, matching the
Telegram bridge and keeping the send path testable without the optional
dependency installed.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from .chat.dedup import EventDedup, chat_lock, sender_allowed
from .chat.render import FEISHU_LIMIT, chunk_html, html_to_lark_md
from .chat.router import CommandRouter
from .chat.transport import ChatTransport

log = logging.getLogger(__name__)

__all__ = ["FeishuPoller", "FeishuTransport", "feishu_enabled"]

#: Feishu (mainland) and Lark (international) are the same API on two hosts.
_DOMAINS = {
    "feishu": "https://open.feishu.cn",
    "lark": "https://open.larksuite.com",
}


def feishu_enabled() -> bool:
    """Whether the optional Feishu command interface is explicitly enabled."""
    return (os.environ.get("ARGUS_SKILL_ENABLE_FEISHU") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _api_base() -> str:
    """Resolve the open-platform host.

    ``ARGUS_SKILL_FEISHU_DOMAIN`` accepts ``feishu`` / ``lark`` or a full URL
    for private deployments.
    """
    raw = (os.environ.get("ARGUS_SKILL_FEISHU_DOMAIN") or "feishu").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw.rstrip("/")
    return _DOMAINS.get(raw.lower(), _DOMAINS["feishu"])


def _reactions_enabled() -> bool:
    return (os.environ.get("ARGUS_SKILL_FEISHU_REACTIONS") or "on").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


# ---------------------------------------------------------------------------
# Feishu API helpers
# ---------------------------------------------------------------------------

_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_TOKEN_GUARD = threading.Lock()


def _request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 10,
) -> dict[str, Any] | None:
    """Issue one open-platform call. Returns parsed JSON, or None on failure."""
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("feishu api %s %s failed: %s", method, url, exc)
        return None


def tenant_access_token(app_id: str, app_secret: str) -> str | None:
    """Fetch (and cache) a tenant access token.

    Feishu tokens last two hours; refresh a minute early so a long-running
    command never sends with a token that expires mid-flight.
    """
    if not app_id or not app_secret:
        return None
    now = time.monotonic()
    with _TOKEN_GUARD:
        cached = _TOKEN_CACHE.get(app_id)
        if cached and cached[1] > now + 60:
            return cached[0]
    data = _request(
        "POST",
        f"{_api_base()}/open-apis/auth/v3/tenant_access_token/internal",
        payload={"app_id": app_id, "app_secret": app_secret},
    )
    if not data or data.get("code") != 0:
        log.warning("feishu: could not obtain tenant access token")
        return None
    token = str(data.get("tenant_access_token") or "")
    if not token:
        return None
    expires_in = int(data.get("expire") or 7200)
    with _TOKEN_GUARD:
        _TOKEN_CACHE[app_id] = (token, time.monotonic() + expires_in)
    return token


def build_card(content: str) -> dict[str, Any]:
    """Wrap ``lark_md`` text in a card.

    Plain Feishu text messages render markdown literally — asterisks and
    backticks show up as characters. A card with a ``lark_md`` block is what
    turns ``/status`` into something readable on a phone.
    """
    return {
        "config": {"wide_screen_mode": True},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
    }


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

class FeishuTransport(ChatTransport):
    """Outbound half of the Feishu bridge."""

    channel = "feishu"
    display_name = "飞书"

    def __init__(self, *, app_id: str, app_secret: str, chat_id: str) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.chat_id = chat_id

    def _token(self) -> str | None:
        return tenant_access_token(self.app_id, self.app_secret)

    def send(self, text: str) -> None:
        token = self._token()
        if not token or not self.chat_id:
            log.warning("feishu: dropping reply (no token or chat_id)")
            return
        url = f"{_api_base()}/open-apis/im/v1/messages?receive_id_type=chat_id"
        for chunk in chunk_html(text, FEISHU_LIMIT):
            card = build_card(html_to_lark_md(chunk))
            _request(
                "POST",
                url,
                token=token,
                payload={
                    "receive_id": self.chat_id,
                    "msg_type": "interactive",
                    "content": json.dumps(card, ensure_ascii=False),
                },
            )

    # -- progress markers --------------------------------------------------

    def begin_progress(self, message_id: str) -> str:
        """React 🤔 on the operator's message while the command runs."""
        if not _reactions_enabled() or not message_id:
            return ""
        token = self._token()
        if not token:
            return ""
        data = _request(
            "POST",
            f"{_api_base()}/open-apis/im/v1/messages/{message_id}/reactions",
            token=token,
            payload={"reaction_type": {"emoji_type": "ThinkingFace"}},
        )
        if not data or data.get("code") != 0:
            return ""
        return str((data.get("data") or {}).get("reaction", {}).get("reaction_id") or "")

    def end_progress(self, message_id: str, handle: str, *, failed: bool = False) -> None:
        if not _reactions_enabled() or not message_id:
            return
        token = self._token()
        if not token:
            return
        if handle:
            _request(
                "DELETE",
                f"{_api_base()}/open-apis/im/v1/messages/{message_id}/reactions/{handle}",
                token=token,
            )
        if failed:
            _request(
                "POST",
                f"{_api_base()}/open-apis/im/v1/messages/{message_id}/reactions",
                token=token,
                payload={"reaction_type": {"emoji_type": "CrossMark"}},
            )


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------

class FeishuPoller:
    """Long-connection listener for inbound Feishu commands.

    Start with :meth:`start` (spawns a daemon thread). The SDK owns reconnects
    and heartbeats; the thread is a daemon so a daemon shutdown tears it down
    with the process.
    """

    def __init__(
        self,
        *,
        life_dir: Path,
        app_id: str | None = None,
        app_secret: str | None = None,
        chat_id: str | None = None,
        allowed_users: str | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.life_dir = life_dir
        env = os.environ.get
        self.app_id = (app_id or env("ARGUS_SKILL_FEISHU_APP_ID") or "").strip()
        self.app_secret = (app_secret or env("ARGUS_SKILL_FEISHU_APP_SECRET") or "").strip()
        self.chat_id = (chat_id or env("ARGUS_SKILL_FEISHU_CHAT_ID") or "").strip()
        self.allowed_users = (
            allowed_users if allowed_users is not None
            else env("ARGUS_SKILL_FEISHU_ALLOWED_USERS", "")
        )
        self._stop = stop_event or threading.Event()
        self._thread: threading.Thread | None = None
        self._dedup = EventDedup(life_dir / "feishu.seen.json")

    @property
    def enabled(self) -> bool:
        return feishu_enabled() and bool(self.app_id and self.app_secret)

    def start(self) -> None:
        if not self.enabled:
            log.info("feishu bridge disabled (missing app id or secret)")
            return
        self._thread = threading.Thread(
            target=self._run, name="feishu-ws", daemon=True
        )
        self._thread.start()
        log.info("feishu bridge started (long connection)")

    # -- inbound -----------------------------------------------------------

    def _router_for(self, chat_id: str) -> CommandRouter:
        return CommandRouter(
            life_dir=self.life_dir,
            transport=FeishuTransport(
                app_id=self.app_id, app_secret=self.app_secret, chat_id=chat_id,
            ),
        )

    def handle_event(self, event: dict[str, Any]) -> None:
        """Process one normalized ``im.message.receive_v1`` payload.

        Split out from the SDK callback so the guard logic — dedup, allowlist,
        text extraction — is exercised by tests without a live connection.
        """
        event_id = str(event.get("event_id") or "")
        if self._dedup.seen(event_id):
            log.debug("feishu: skipping duplicate event %s", event_id)
            return
        chat_id = str(event.get("chat_id") or "")
        text = str(event.get("text") or "").strip()
        sender_id = str(event.get("sender_id") or "")
        message_id = str(event.get("message_id") or "")
        if not chat_id or not text:
            return
        if not sender_allowed(sender_id, self.allowed_users):
            log.info("feishu: ignoring message from sender outside the allowlist")
            return

        # The SDK's socket thread must stay free for heartbeats, and a command
        # can take minutes, so dispatch on a worker. The per-chat lock keeps
        # two messages from the same conversation from interleaving.
        def _work() -> None:
            transport = FeishuTransport(
                app_id=self.app_id, app_secret=self.app_secret, chat_id=chat_id,
            )
            router = CommandRouter(life_dir=self.life_dir, transport=transport)
            handle = transport.begin_progress(message_id)
            failed = False
            with chat_lock(chat_id):
                try:
                    log.info("feishu command: %s", text[:80])
                    router.dispatch(text)
                except Exception:  # noqa: BLE001
                    failed = True
                    log.exception("feishu: command failed")
                    transport.send("❌ 命令执行失败，请查看守护进程日志。")
                finally:
                    transport.end_progress(message_id, handle, failed=failed)

        self._spawn(_work, f"feishu-command-{message_id[-8:] or '?'}")

    def _spawn(self, work: Any, name: str) -> None:
        """Run *work* off the socket thread. Overridden in tests to run inline."""
        threading.Thread(target=work, name=name, daemon=True).start()

    @staticmethod
    def _normalize(data: Any) -> dict[str, Any]:
        """Flatten the SDK's ``P2ImMessageReceiveV1`` into a plain dict."""
        header = getattr(data, "header", None)
        event = getattr(data, "event", None)
        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        text = ""
        if getattr(message, "message_type", "") == "text":
            try:
                text = json.loads(getattr(message, "content", "") or "{}").get("text", "")
            except (ValueError, TypeError):
                text = ""
        sender_id = ""
        inner = getattr(sender, "sender_id", None)
        if inner is not None:
            sender_id = str(getattr(inner, "open_id", "") or "")
        return {
            "event_id": str(getattr(header, "event_id", "") or ""),
            "chat_id": str(getattr(message, "chat_id", "") or ""),
            "message_id": str(getattr(message, "message_id", "") or ""),
            "text": text,
            "sender_id": sender_id,
        }

    def _run(self) -> None:
        try:
            import lark_oapi as lark
        except ImportError:
            log.warning(
                "feishu bridge enabled but lark-oapi is not installed; "
                "run `pip install 'argus-skill[feishu]'` to activate it"
            )
            return

        def _on_message(data: Any) -> None:
            try:
                self.handle_event(self._normalize(data))
            except Exception:  # noqa: BLE001
                log.exception("feishu: failed to handle inbound event")

        handler_builder = lark.EventDispatcherHandler.builder("", "")
        handler_builder = handler_builder.register_p2_im_message_receive_v1(_on_message)
        # Reaction and read-receipt events arrive on the same socket. Without
        # handlers the SDK logs "processor not found" for each one, which buries
        # the daemon's own logs; the bridge does not act on them.
        for noisy in (
            "register_p2_im_message_reaction_created_v1",
            "register_p2_im_message_reaction_deleted_v1",
            "register_p2_im_message_message_read_v1",
        ):
            register = getattr(handler_builder, noisy, None)
            if register is not None:
                handler_builder = register(lambda _event: None)
        handler = handler_builder.build()
        client = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )
        log.info(
            "feishu: dialing long connection (allowlist=%s)",
            self.allowed_users or "everyone",
        )
        try:
            client.start()
        except Exception:  # noqa: BLE001
            log.exception("feishu: long connection ended")
