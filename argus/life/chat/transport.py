"""The interface a messaging platform implements to host the command surface.

:class:`CommandRouter` never talks to Telegram or Feishu directly — it hands
finished replies to a ``ChatTransport``. Replies are written once, in
Telegram-flavoured HTML (``<b>``/``<code>``/``<pre>``, entity-escaped body
text), because that is the markup the command bodies already used when they
lived inside ``telegram_bot``. Each transport renders that canonical form into
whatever its platform accepts — see :mod:`argus_skill.life.chat.render`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InboundMessage:
    """One operator message, normalized across platforms."""

    channel: str
    chat_id: str
    text: str
    sender_id: str = ""
    message_id: str = ""
    event_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ChatTransport(ABC):
    """Outbound half of a chat channel.

    ``channel`` is the short machine name used for inbox provenance
    (``telegram.nudge``, ``feishu.free_text``); ``display_name`` is what the
    operator sees in help text.
    """

    channel: str = "chat"
    display_name: str = "Chat"

    @abstractmethod
    def send(self, text: str) -> None:
        """Deliver one reply, splitting it if the platform caps message size.

        ``text`` is canonical Telegram-flavoured HTML. Implementations must not
        drop content: a reply longer than the platform limit is split across
        several messages, never truncated.
        """

    # -- optional progress affordances ------------------------------------
    # Platforms that can annotate the operator's own message (Feishu
    # reactions) override these; the default is a no-op so the router stays
    # identical across channels.

    def begin_progress(self, message_id: str) -> str:
        """Mark *message_id* as being worked on. Returns an opaque handle."""
        return ""

    def end_progress(self, message_id: str, handle: str, *, failed: bool = False) -> None:
        """Clear the marker set by :meth:`begin_progress`."""

    def describe(self) -> str:
        return self.display_name
