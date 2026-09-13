"""Advisory messages between projects owned by one Argus user/tenant."""

from .store import PeerMailbox, mailbox_for_project

__all__ = ["PeerMailbox", "mailbox_for_project"]
