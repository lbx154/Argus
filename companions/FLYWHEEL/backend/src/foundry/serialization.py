from __future__ import annotations

from typing import Any

from .db import decode_row, decode_rows


def connection_public(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    output = decode_row(row) or {}
    token_ref = output.pop("token_ref", None)
    output["has_token"] = bool(token_ref)
    output["token_source"] = "environment" if str(token_ref or "").startswith("env:") else (
        "memory" if token_ref else None
    )
    return output


def connections_public(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [connection_public(row) or {} for row in rows]


__all__ = ["decode_row", "decode_rows", "connection_public", "connections_public"]
