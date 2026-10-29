These records came from Chromium driving the built Argus cockpit at port 18801,
with an isolated ARGUS_HOME and a controlled Codex app-server fixture; no model ran.
The browser sent the same Chinese question twice and then a CSV attachment, and
refreshed the conversation. The viewports were 1440 × 1000 and 390 × 1000 pixels.
The desktop records are kept here; both viewport captures and the browser scripts
are in the main workspace's `.argus/conversation-fixes/t1/` evidence directory.

`before.json` keeps the event lines and outbound requests captured from commit
`a04c0cf171e54ee7af8c83bf3c90a52e26237f42`: request ids were lost from the record,
the attachment suffix was stored as prose, and file answers read as task completion.
`after.json` keeps the same scenario after the fixes, including actual request ids
and upload metadata. The browser clock was one hour ahead for the first send and
one hour behind for the repeated send. The second send was held at the HTTP
boundary long enough to inspect its local row before the server recorded it.

Since 10bdb5598 a Manager turn and its letter name the message they answer: `answers` was
added to the Manager's `call.start` and `handoff` lines in `after.json`, each naming the message
just before it, as the record now carries them.
