"""Advisory messages between projects owned by one Argus user/tenant.

Layer: runtime

Cross-project advisory messages (store, inbox, transport, handler) that sit
beside ``life`` and ``manager``; imports ``manager`` and ``life`` within the
runtime layer and ``advisor`` below it."""

from .store import PeerMailbox, mailbox_for_project

__all__ = ["PeerMailbox", "mailbox_for_project"]
