"""Reply chunking and markup translation.

The chunker replaced a truncating sender, so the property that actually
matters is that nothing is dropped — and that each piece is still valid markup
on its own.
"""
from __future__ import annotations

from argus.life.chat.render import (
    TELEGRAM_LIMIT,
    chunk_html,
    html_to_lark_md,
    html_to_plain,
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


def test_long_reply_is_split_not_truncated() -> None:
    line = "x" * 200
    text = "\n".join([line] * 100)  # ~20k chars

    chunks = chunk_html(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= TELEGRAM_LIMIT for chunk in chunks)
    # Every source line survives somewhere — the old sender kept only the first
    # 4090 characters.
    assert _strip_pre_scaffolding(chunks) == text


def test_pre_block_stays_balanced_across_a_split() -> None:
    body = "\n".join(f"row {i} " + "y" * 120 for i in range(200))
    text = f"<pre>{body}</pre>"

    chunks = chunk_html(text)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.count("<pre>") == chunk.count("</pre>")
        assert len(chunk) <= TELEGRAM_LIMIT
    assert _strip_pre_scaffolding(chunks) == text


def test_split_does_not_land_inside_an_entity_or_tag() -> None:
    text = "&amp;" * 3000

    for chunk in chunk_html(text):
        # A chunk ending mid-entity would leave a bare "&am".
        assert not chunk.endswith("&")
        assert "&am" not in chunk.replace("&amp;", "")


def test_single_line_longer_than_the_limit_is_hard_split() -> None:
    text = "z" * (TELEGRAM_LIMIT * 3)

    chunks = chunk_html(text)

    assert len(chunks) >= 3
    assert all(len(chunk) <= TELEGRAM_LIMIT for chunk in chunks)
    assert "".join(chunks) == text


def test_lark_md_translation() -> None:
    html = "<b>状态</b>\n<code>abc</code>\n<pre>line1\nline2</pre>"

    out = html_to_lark_md(html)

    assert "**状态**" in out
    assert "`abc`" in out
    assert "```" in out
    assert "<" not in out


def test_lark_md_unescapes_after_translating_tags() -> None:
    # A literal "<b>" in command output is escaped as &lt;b&gt; upstream; it
    # must come back as text, not become real markup.
    assert html_to_lark_md("&lt;b&gt;") == "<b>"


def test_plain_rendering_strips_every_tag() -> None:
    assert html_to_plain("<b>hi</b> &amp; bye") == "hi & bye"
