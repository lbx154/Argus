"""A Reviewer's editable report, with a file-bound MCP write capability."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


class ReviewFileStore:
    """Only the host chooses the output path; tools accept prose, never paths."""

    def __init__(self, path: str, receipt: str):
        target = Path(path).absolute()
        if target.name != "REVIEW.md" or target.parent.name != "paper":
            raise ValueError("review output must be paper/REVIEW.md")
        self.path = target.parent.resolve() / target.name
        self.receipt = Path(receipt).absolute()
        if self.path == self.receipt:
            raise ValueError("review tracking file must be separate from the report")

    def read_review(self) -> str:
        if self.path.is_symlink():
            raise ValueError("the review output cannot be a symlink")
        return self.path.read_text(encoding="utf-8") if self.path.exists() else ""

    def write_review(self, text: str) -> dict[str, str | int]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("write a nonempty natural-language review")
        if self.path.is_symlink():
            raise ValueError("the review output cannot be a symlink")
        from ..core.secret_guard import known_secret_values, redact_secrets_text
        from ..manager.source_writeback import atomic_write

        text = redact_secrets_text(text, known_values=known_secret_values())
        atomic_write(self.path, text)
        atomic_write(self.receipt, json.dumps({
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }))
        return {"path": str(self.path), "characters": len(text)}

    def authored_review(self) -> str | None:
        """A preexisting Engineer-editable report is not this review's output."""
        if not self.receipt.is_file():
            return None
        expected = json.loads(self.receipt.read_text(encoding="utf-8"))["sha256"]
        text = self.read_review()
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != expected:
            raise ValueError("the review file changed after the Reviewer's write")
        return text


def copilot_review_file_args(output: dict[str, str]) -> list[str]:
    store = ReviewFileStore(**output)
    config = {"mcpServers": {"argus_review": {
        "type": "local", "command": sys.executable,
        "args": ["-m", "argus_skill.reviewer.review_file", "--path", str(store.path),
                 "--receipt", str(store.receipt)],
        "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        "tools": ["read_review", "write_review"],
    }}}
    return ["--additional-mcp-config", json.dumps(config)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    store = ReviewFileStore(args.path, args.receipt)
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("argus_review", json_response=True, log_level="ERROR")

    @server.tool()
    def read_review() -> str:
        """Read your current paper/REVIEW.md before updating this round's opinion."""
        return store.read_review()

    @server.tool()
    def write_review(text: str) -> dict[str, str | int]:
        """Replace your paper/REVIEW.md with your complete natural-language review.

        You can call this again to edit your opinion. This tool cannot edit the
        paper, figures, code, or experiment evidence. No review template or
        structured recommendation fields are required.
        """
        return store.write_review(text)

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
