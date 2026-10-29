Captured on 2026-10-29 from the unmodified a04c0cf171e54ee7af8c83bf3c90a52e26237f42 build,
served on port 18802 with an isolated state root and a controllable Codex app-server proxy.
The browser sent one operator message, stopped the waiting reply and resumed it. This is the
record through the resumed Manager's fourteenth message; it includes the interrupted call's end.
No fixture fields were invented or paths substituted. The proxy emits the messages deterministically.
The complete browser evidence and harness live in `.argus/conversation-fixes/t2/` in the main workspace.

Since 10bdb5598 every operator message is named and a Manager turn names the message it answers:
the message's `request_id` and the Manager's `answers` were added to these lines, as the record
now carries them.
