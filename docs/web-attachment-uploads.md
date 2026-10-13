# Web attachments

The conversation file picker, drop and paste paths accept up to **100 MiB
(104,857,600 bytes) per file**, five files and **500 MiB per message**. Supported
formats remain PNG, JPEG, WebP, PDF, UTF-8 Markdown/text, JSON and CSV. Selecting
files does not upload them or start a task.

The browser reads the project upload limits, then sends one file at a time in
requests of at most 4 MiB. Uploading, validating and file-ready states are
distinct from sending a message or an agent reading its attachments. Network
failures get at most two recovery attempts: query the actual offset first,
then send only missing bytes. Agent message requests are never automatically
replayed.

Completed attachment references are saved with the project draft, not file
bytes. A failed message can reuse them. After a refresh, completed references
are available without reselecting files; incomplete files must be selected
again and uploaded afresh. Browser storage failures are shown explicitly.
Removing a completed reference from a draft does not delete the server file.

## Storage and validation

Uploads use the existing project-scoped `att-*` identity, guarded workspace
directories and attachment metadata. Incomplete bytes are stored separately
from the final filename; validation and an atomic metadata write register the
completed attachment. An interrupted final rename can be completed again.
An acknowledged offset follows a flushed, synced write; duplicate or stale
offsets return 409 rather than appending twice.

Admission reserves at most 500 MiB/five incomplete files per project and
2 GiB/20 per instance. Two chunk requests may be in flight at once. Storage
keeps 256 MiB free. Pending reservations are read from disk, including after
restart. Hourly and on-admission cleanup reclaims uploads idle for 24 hours;
completed attachments are never expired by this cleanup.

Text is decoded incrementally. JSON syntax uses `ijson`, without constructing
a document object; to bound parser allocation, individual keys/values are
limited to 4 MiB and nesting to 128 levels. Exceeding either limit is an explicit
validation error, not a truncated success. PDF/image checks retain the existing
signature validation; upload success is not a claim that every page is valid.

Text/CSV previews remain bounded and show truncation. PDF previews use
authenticated byte-range requests instead of fetching the whole file into an
ArrayBuffer. Images above 4 MiB are not decoded for composer thumbnails.
Original files remain downloadable.

## API and compatibility

- `GET /api/projects/{sid}/attachments/limits`
- `POST /api/projects/{sid}/attachments/uploads` with `name`, `mime`, `size_bytes`
- `GET /api/projects/{sid}/attachments/uploads/{attachment_id}`
- `PUT /api/projects/{sid}/attachments/uploads/{attachment_id}?offset=N`
  with an `application/octet-stream` body of at most 4 MiB
- `POST /api/projects/{sid}/attachments/uploads/{attachment_id}/complete`
- `DELETE /api/projects/{sid}/attachments/uploads/{attachment_id}`

All routes require the existing authentication and project ownership. Finished
uploads return the same metadata and references used by existing messages.
Canceling a completed upload never deletes its attachment. JSON request limits
and other API/WebSocket limits are unchanged. The legacy multipart endpoint
retains its 10 MiB/file, 25 MiB/request bounds for old clients.

Deploy the matching backend before the new page. This feature does not enable
customer invitations, change the trial hostname/token, introduce a storage
service or change model settings. On rollback, completed files retain the old
metadata shape; the old 10 MiB implementation cannot reference files over its
own size limit, and incomplete uploads require the chunk-capable version.
