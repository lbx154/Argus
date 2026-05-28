from __future__ import annotations

ATTACHMENT_CONFIRM_THRESHOLD = 5
ATTACHMENT_CONFIRM_COMMAND = "/confirm-send"
ATTACHMENT_CANCEL_COMMAND = "/cancel-send"
BOT_ATTACHMENT_SOURCES = {"telegram", "feishu"}


def requires_attachment_confirmation(*, source: str, attachment_count: int) -> bool:
    return source in BOT_ATTACHMENT_SOURCES and attachment_count > ATTACHMENT_CONFIRM_THRESHOLD


def format_attachment_confirmation_message(*, attachment_count: int) -> str:
    return (
        f"[btw] matched {attachment_count} files.\n"
        f"Because attachment count is greater than {ATTACHMENT_CONFIRM_THRESHOLD}, "
        "sending is paused for confirmation.\n"
        f"Send `{ATTACHMENT_CONFIRM_COMMAND}` to continue, or `{ATTACHMENT_CANCEL_COMMAND}` to skip."
    )
