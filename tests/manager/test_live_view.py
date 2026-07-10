from __future__ import annotations

import json

from argus_skill.manager.live_view import (
    LIVE_VIEW_MANIFEST,
    LiveViewDecision,
    apply_live_view_decision,
    load_live_view_decision,
    normalize_live_view_path,
)


def test_live_view_paths_are_workspace_relative_and_secret_safe() -> None:
    assert normalize_live_view_path("paper/main.pdf") == "paper/main.pdf"
    for unsafe in (
        "../secret.txt",
        "/etc/passwd",
        ".env",
        ".env.local",
        ".git/config",
        "keys/service.pem",
        "credentials.json",
    ):
        assert normalize_live_view_path(unsafe) is None


def test_live_view_round_trip_and_explicit_clear(tmp_path) -> None:
    view = LiveViewDecision(
        title="Current proof",
        paths=("research/PROOF.md", "paper/main.pdf"),
        reason="These are the live deliverables.",
    )
    apply_live_view_decision(tmp_path, decided=True, view=view)

    assert load_live_view_decision(tmp_path) == view
    payload = json.loads((tmp_path / LIVE_VIEW_MANIFEST).read_text(encoding="utf-8"))
    assert payload["paths"] == ["research/PROOF.md", "paper/main.pdf"]

    apply_live_view_decision(tmp_path, decided=False, view=None)
    assert load_live_view_decision(tmp_path) == view
    apply_live_view_decision(tmp_path, decided=True, view=None)
    assert load_live_view_decision(tmp_path) is None
