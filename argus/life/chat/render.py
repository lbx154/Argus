"""Reply chunking and per-platform markup rendering.

Commands compose replies in Telegram-flavoured HTML. Two things have to happen
before those replies reach a phone:

* **Chunking.** Telegram rejects messages over 4096 characters. The bridge used
  to truncate at 4090 and append an ellipsis, which silently dropped the tail of
  every long ``/journal`` or ``/backlog``. :func:`chunk_html` splits instead,
  and keeps ``<pre>`` blocks balanced across the split so Telegram's HTML parser
  still accepts each piece.
* **Translation.** Feishu does not speak HTML; :func:`html_to_lark_md` converts
  the same canonical reply into ``lark_md`` for an interactive card.
"""
from __future__ import annotations

import re

#: Telegram's hard cap on ``sendMessage`` text.
TELEGRAM_LIMIT = 4096
#: Feishu cards accept far more, but shorter cards scroll better on a phone.
FEISHU_LIMIT = 4000

_PRE_OPEN = "<pre>"
_PRE_CLOSE = "</pre>"
_TAG_RE = re.compile(r"<[^>]*>")


def _safe_cut(text: str, limit: int) -> int:
    """Largest cut index ``<= limit`` that lands outside a tag or entity.

    Slicing mid-``<pre`` or mid-``&amp;`` would hand Telegram unparseable
    markup, so back off to just before the offending fragment.
    """
    if limit >= len(text):
        return len(text)
    cut = limit
    # Don't cut inside "<...>".
    open_tag = text.rfind("<", 0, cut)
    if open_tag != -1 and text.find(">", open_tag, cut) == -1:
        cut = open_tag
    # Don't cut inside "&...;" (entities are short; 12 chars is generous).
    amp = text.rfind("&", max(0, cut - 12), cut)
    if amp != -1 and text.find(";", amp, cut) == -1:
        cut = amp
    return cut if cut > 0 else limit


def _split_long_line(line: str, budget: int) -> list[str]:
    """Break one over-long line into ``<= budget`` pieces at safe offsets."""
    if len(line) <= budget:
        return [line]
    pieces: list[str] = []
    rest = line
    while len(rest) > budget:
        cut = _safe_cut(rest, budget)
        pieces.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        pieces.append(rest)
    return pieces


def chunk_html(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split *text* into sendable chunks, preserving ``<pre>`` balance.

    Splits on line boundaries where possible. A ``<pre>`` block spanning a
    split is closed at the end of one chunk and reopened at the start of the
    next, so every chunk is independently well-formed.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    # Reserve room for a reopened/closed <pre> pair on any chunk.
    budget = max(1, limit - len(_PRE_OPEN) - len(_PRE_CLOSE))
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    open_at_start = False
    depth = 0

    def flush() -> None:
        nonlocal buf, buf_len, open_at_start
        if not buf:
            return
        body = "\n".join(buf)
        if open_at_start:
            body = _PRE_OPEN + body
        if depth > 0:
            body = body + _PRE_CLOSE
        chunks.append(body)
        open_at_start = depth > 0
        buf = []
        buf_len = 0

    for raw_line in text.split("\n"):
        for piece in _split_long_line(raw_line, budget):
            cost = len(piece) + (1 if buf else 0)
            if buf and buf_len + cost > budget:
                flush()
                cost = len(piece)
            buf.append(piece)
            buf_len += cost
            depth = max(0, depth + piece.count(_PRE_OPEN) - piece.count(_PRE_CLOSE))
    flush()
    return [c for c in chunks if c.strip()] or [text[:limit]]


def html_to_lark_md(text: str) -> str:
    """Render canonical reply HTML as Feishu ``lark_md``.

    Tags are translated before entities are unescaped — the other order would
    turn a literal ``&lt;b&gt;`` in command output into real markup.
    """
    out = text
    out = out.replace(_PRE_OPEN, "```\n").replace(_PRE_CLOSE, "\n```")
    out = out.replace("<code>", "`").replace("</code>", "`")
    out = out.replace("<b>", "**").replace("</b>", "**")
    out = out.replace("<strong>", "**").replace("</strong>", "**")
    out = out.replace("<i>", "*").replace("</i>", "*")
    out = out.replace("<em>", "*").replace("</em>", "*")
    out = out.replace("<br>", "\n").replace("<br/>", "\n")
    out = _TAG_RE.sub("", out)
    out = out.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return out


def html_to_plain(text: str) -> str:
    """Strip every tag and unescape entities — for platforms without markup."""
    out = _TAG_RE.sub("", text)
    return out.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
