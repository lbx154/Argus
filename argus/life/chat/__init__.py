"""Channel-agnostic chat command surface shared by every IM bridge.

The daemon exposes the same operator commands (``/add``, ``/status``,
``/nudge`` …) on more than one messaging platform. Everything platform
independent lives here so a new channel only has to implement
:class:`~argus_skill.life.chat.transport.ChatTransport`:

* :mod:`.transport` — the ``ChatTransport`` interface and ``InboundMessage``.
* :mod:`.router`    — ``CommandRouter``: parses a message, runs the command.
* :mod:`.render`    — reply chunking and per-platform markup rendering.
* :mod:`.dedup`     — event de-duplication, sender allowlists, per-chat locks.

Concrete channels: :mod:`argus_skill.life.telegram_bot` (long polling) and
:mod:`argus_skill.life.feishu_bot` (Feishu/Lark WebSocket long connection).
"""

from .dedup import EventDedup, chat_lock, sender_allowed
from .render import TELEGRAM_LIMIT, chunk_html, html_to_lark_md
from .router import CommandRouter
from .transport import ChatTransport, InboundMessage

__all__ = [
    "TELEGRAM_LIMIT",
    "ChatTransport",
    "CommandRouter",
    "EventDedup",
    "InboundMessage",
    "chat_lock",
    "chunk_html",
    "html_to_lark_md",
    "sender_allowed",
]
