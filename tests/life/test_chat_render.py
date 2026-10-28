"""Reply chunking and markup translation.

The chunker replaced a truncating sender, so the property that actually
matters is that nothing is dropped — and that each piece is still valid markup
on its own.
"""
from __future__ import annotations

from argus.life.chat.render import (
    TELEGRAM_LIMIT,
    chunk_html,
)


def _strip_pre_scaffolding(chunks: list[str]) -> str:
    """Rejoin chunks, dropping only the <pre> tags the chunker itself added."""
    body = []
    for index, chunk in enumerate(chunks):
        text = chunk
        if index > 0 and text.startswith("<pre>"):
            text = text[len("<pre>"):]
        if index < len(chunks) - 1 and text.endswith("</pre>"):
            text = text[: -len("</pre>")]
        body.append(text)
    return "\n".join(body)


def test_short_text_is_not_split() -> None:
    assert chunk_html("hello") == ["hello"]


def test_empty_text_produces_no_messages() -> None:
    assert chunk_html("") == []


def test_pre_block_stays_balanced_across_a_split() -> None:
    body = "\n".join(f"row {i} " + "y" * 120 for i in range(200))
    text = f"<pre>{body}</pre>"

    chunks = chunk_html(text)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.count("<pre>") == chunk.count("</pre>")
        assert len(chunk) <= TELEGRAM_LIMIT
    assert _strip_pre_scaffolding(chunks) == text
