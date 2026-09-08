from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from argus_skill.reviewer.review_file import ReviewFileStore


def test_only_an_authored_current_report_is_used(tmp_path: Path):
    report = tmp_path / "paper/REVIEW.md"
    report.parent.mkdir()
    report.write_text("An older opinion.")
    store = ReviewFileStore(str(report), str(tmp_path / "receipt.json"))
    assert store.authored_review() is None
    store.write_review("The first current assessment.")
    assert store.authored_review() == "The first current assessment."
    store.write_review("A revised current assessment, with resolved concerns.")
    assert report.read_text() == "A revised current assessment, with resolved concerns."
    report.write_text("A concurrent unauthored replacement.")
    with pytest.raises(ValueError, match="changed"):
        store.authored_review()


def test_review_write_cannot_be_redirected_to_the_manuscript(tmp_path: Path):
    paper = tmp_path / "paper"
    paper.mkdir()
    manuscript = paper / "main.tex"
    manuscript.write_text("Actual paper")
    with pytest.raises(ValueError, match="REVIEW.md"):
        ReviewFileStore(str(manuscript), str(tmp_path / "receipt.json"))
    report = paper / "REVIEW.md"
    report.symlink_to(manuscript)
    store = ReviewFileStore(str(report), str(tmp_path / "receipt.json"))
    with pytest.raises(ValueError, match="symlink"):
        store.write_review("Cannot replace the paper")
    assert manuscript.read_text() == "Actual paper"


def test_mcp_tool_revises_the_same_file_without_a_review_schema(tmp_path: Path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    report = tmp_path / "paper/REVIEW.md"
    marker = tmp_path / "receipt.json"

    async def run():
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "argus_skill.reviewer.review_file", "--path", str(report), "--receipt", str(marker)],
            env=env,
        )
        async with stdio_client(server) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert set(tools) == {"read_review", "write_review"}
            assert set(tools["write_review"].inputSchema["properties"]) == {"text"}
            for prose in ["这项对照有价值，请补充未见种子实验。", "新增实验已经解决该问题，当前建议 weak accept。"]:
                result = await session.call_tool("write_review", {"text": prose})
                assert not result.isError
                assert report.read_text() == prose
            assert ReviewFileStore(str(report), str(marker)).authored_review() == prose
            result = await session.call_tool("read_review", {})
            assert prose in result.content[0].text

    asyncio.run(run())
