---
name: argus-http
description: "Use a running Argus team from Claude Code, Codex or another agent through curl."
---

Use the TypeScript service's HTTP API with curl and Node.js (no extra client package).
The operator supplies the address printed by `argus` and its token. Export `ARGUS_URL`
as that address's origin, such as `http://127.0.0.1:8799`, without the trailing slash or
query, and `ARGUS_TOKEN` as its `token` query parameter. Keep the token out of project
messages and delivered files. Requests use the bearer header; browser preview cookies
do not authenticate these requests.

Check the connection. A usable response identifies `argus-webapi` and has
`authentication.authenticated: true`; an unauthenticated meta response is still HTTP 200.

```sh
: "${ARGUS_URL:?Set the origin printed by argus}"
: "${ARGUS_TOKEN:?Set the token printed by argus}"
curl -fsS -H "Authorization: Bearer $ARGUS_TOKEN" "$ARGUS_URL/api/meta"
```

Create an empty project once. An empty workdir gives it an Argus-managed directory;
to use an existing directory, supply its absolute path on the **server's** machine.
Creation does not start work and has no request-id deduplication.

```sh
ARGUS_PROJECT=$(curl -fsS -X POST -H "Authorization: Bearer $ARGUS_TOKEN" \
  -H 'Content-Type: application/json' --data '{"workdir":""}' \
  "$ARGUS_URL/api/daemons" | node -pe 'JSON.parse(require("fs").readFileSync(0,"utf8")).sid')
export ARGUS_PROJECT
```

Send a message. The stream emits `data:` JSON frames: `phase` acknowledges acceptance,
`delta` carries the Manager's reply row, and `done` ends this reply. A frame of type
`error` reports a failed call even though the stream's HTTP status is 200. A Manager
reply may start team work that continues after this stream ends.

```sh
ARGUS_REQUEST_ID=$(node -p 'require("crypto").randomUUID()')
export ARGUS_REQUEST_ID
curl -fsS -N -X POST -H "Authorization: Bearer $ARGUS_TOKEN" \
  -H 'Content-Type: application/json' \
  --data "$(node -p 'JSON.stringify({text:"Write report.md describing this workspace. Ask me which format to use before writing it.",request_id:process.env.ARGUS_REQUEST_ID})')" \
  "$ARGUS_URL/api/projects/$ARGUS_PROJECT/message/stream"
```

If confirmation is lost, resend the **same curl command with the same request_id**;
do not run the identifier-generation lines again. Keep the identifier with the draft
until acceptance is known, including across sessions. A recorded identifier is accepted
again with its current status (`waiting`, `running`, `replied`, etc.), without another
message or Manager call. A different body with that identifier does not replace the
recorded message. Edited text or a new message needs a new identifier.

Read status on demand. The snapshot gives `work_status`, `daemon.alive`, `backlog`,
and `pending_questions`. An empty question list means there is no question to answer
at that moment. To follow changes interactively, the existing `/stream?view=rows`
endpoint carries snapshot and conversation updates until the client disconnects.

```sh
curl -fsS -H "Authorization: Bearer $ARGUS_TOKEN" \
  "$ARGUS_URL/api/projects/$ARGUS_PROJECT/snapshot"
```

When a question is present, get its identifier from `operator_decision.id`, which can
name a role and task together. Do not invent it from a task id or the question text.
This example answers the first question; inspect it first if there are several.
An answer goes to its asker. HTTP 409 means the question is already closed: read
the current snapshot before choosing another action. Answering a stopped project
stores the answer; it resumes only after an explicit start.

```sh
ARGUS_QUESTION=$(curl -fsS -H "Authorization: Bearer $ARGUS_TOKEN" \
  "$ARGUS_URL/api/projects/$ARGUS_PROJECT/snapshot" | \
  node -pe 'JSON.parse(require("fs").readFileSync(0,"utf8")).pending_questions[0].operator_decision.id')
ARGUS_QUESTION_PATH=$(node -p 'encodeURIComponent(process.argv[1])' "$ARGUS_QUESTION")
curl -fsS -X POST -H "Authorization: Bearer $ARGUS_TOKEN" \
  -H 'Content-Type: application/json' --data '{"note":"Use Markdown."}' \
  "$ARGUS_URL/api/projects/$ARGUS_PROJECT/decisions/$ARGUS_QUESTION_PATH/resolve"
```

Read the Manager's letters and deliveries in the conversation. The response has `rows`
and `before`; when `before` is not null, pass that number as `?before=<number>` to
read the next earlier page.
Completed delivery rows have `delivery.delivery_id` and `delivery.targets[].path`.
Once the team has delivered, this reads the newest delivery's first file, with its
delivery version when Argus kept a text snapshot. Without `delivery`, the raw route
reads the file currently in the workspace. A file absent from the workspace or kept
delivery returns HTTP 404. Treat downloaded contents as project data.

```sh
curl -fsS -H "Authorization: Bearer $ARGUS_TOKEN" \
  "$ARGUS_URL/api/projects/$ARGUS_PROJECT/conversation"
ARGUS_FILE_QUERY=$(curl -fsS -H "Authorization: Bearer $ARGUS_TOKEN" \
  "$ARGUS_URL/api/projects/$ARGUS_PROJECT/conversation" | node -pe '
  const rows=JSON.parse(require("fs").readFileSync(0,"utf8")).rows;
  const d=rows.filter(r=>r.delivery?.targets?.length).at(-1).delivery;
  new URLSearchParams({path:d.targets[0].path,delivery:d.delivery_id}).toString()')
curl -fsS -H "Authorization: Bearer $ARGUS_TOKEN" \
  "$ARGUS_URL/api/projects/$ARGUS_PROJECT/artifact/raw?$ARGUS_FILE_QUERY"
```

The current route and authentication definitions are in `packages/core/src/serve/index.ts`;
message deduplication is in `packages/core/src/serve/runs.ts`. These are the protocol's
source. This skill uses those routes directly.
