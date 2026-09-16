# Browser pairing links

An authenticated owner can POST `/api/pairing-links` to issue a one-use link,
returned as a relative `path` with `expires_in=3600`. The path contains a temporary
random code, so the permanent Web token does not need to be pasted into a chat.

Opening the link shows a small connection page. GET requests and link previews do
not consume it. Clicking Connect submits a POST and redirects through the existing
browser token-adoption flow; the application stores the token and clears it from
the URL. Ordinary refreshes and visits to the site's root remain paired.

Issuance requires the existing owner authentication. Links expire after one hour,
are consumed atomically, and are invalidated by a server restart. At most 32 pending
links are retained in memory, as hashes. Pairing continues to use the existing
shared workbench identity; this does not add user accounts or project permissions.
Responses prevent caching, framing and referrer forwarding. No authentication is
removed from existing APIs.

The recovery banner describes site pairing instead of requiring Argus Desktop.
Its input also accepts a temporary pairing link for the current origin.
