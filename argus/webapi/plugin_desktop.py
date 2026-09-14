"""First-party desktop adapter for a pinned plugin's public HTML controls.

The installed wheel stays byte-identical. Unknown plugin versions retain their
normal browser UI rather than receiving an unverified DOM adaptation.
"""
from __future__ import annotations

from pathlib import Path

ADAPTER_NAME = "_argus_desktop_v1.js"
_CRYSTAL_DIGEST = "030f22e7c18cb411db43f42fc4667cba06da1593a8a2054cc1a8e7e1fd12fed2"
_MAX_HTML = 256 * 1024


def supported(spec: dict) -> bool:
    return (
        spec.get("id") == "crystalpilot"
        and spec.get("version") == "0.4.0"
        and spec.get("host_api") == 1
        and spec.get("artifact", {}).get("sha256") == _CRYSTAL_DIGEST
    )


def script_bytes() -> bytes:
    return Path(__file__).with_name("plugin_desktop.js").read_bytes()


def html_adapter(send):
    """Preserve streaming/non-HTML responses; buffer only a small HTML document."""
    start = None
    chunks: list[bytes] | None = None
    size = 0

    async def wrapped(message):
        nonlocal start, chunks, size
        if message["type"] == "http.response.start":
            headers = dict(message.get("headers", []))
            if (message.get("status") == 200
                    and b"text/html" in headers.get(b"content-type", b"")
                    and not headers.get(b"content-encoding")):
                start, chunks = message, []
                return
        elif message["type"] == "http.response.body" and chunks is not None:
            chunks.append(message.get("body", b""))
            size += len(chunks[-1])
            if size > _MAX_HTML:
                await send(start)
                await send({**message, "body": b"".join(chunks)})
                chunks = None
                return
            if message.get("more_body", False):
                return
            body = b"".join(chunks)
            marker = b"</head>"
            if marker in body:
                tag = (f'<script defer src="/plugins/crystalpilot/{ADAPTER_NAME}"></script>\n').encode()
                body = body.replace(marker, tag + marker, 1)
                headers = [(k, v) for k, v in start.get("headers", [])
                           if k.lower() not in {b"content-length", b"etag", b"last-modified", b"cache-control"}]
                headers += [(b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]
                start = {**start, "headers": headers}
            await send(start)
            await send({**message, "body": body})
            chunks = None
            return
        await send(message)

    return wrapped
