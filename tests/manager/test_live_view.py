from __future__ import annotations

import json

import pytest

from argus_skill.manager.live_view import (
    LIVE_VIEW_MANIFEST,
    LiveViewDecision,
    apply_live_view_decision,
    apply_manager_rendering_response,
    load_live_view_decision,
    manager_rendering_prompt,
    normalize_live_view_path,
    parse_live_view_response,
)


def test_live_view_paths_are_workspace_relative_and_secret_safe() -> None:
    assert normalize_live_view_path("paper/main.pdf") == "paper/main.pdf"
    assert normalize_live_view_path("pyproject.toml") == "pyproject.toml"
    assert normalize_live_view_path(".argus/live/current.md") == ".argus/live/current.md"
    for unsafe in (
        "../secret.txt",
        "/etc/passwd",
        ".env",
        ".env.local",
        ".git/config",
        ".argus/live-view.json",
        ".argus/live/.ssh/config",
        ".argus/live/nested/current.md",
        ".npmrc",
        "private/token.txt",
        "config/service-account.json",
        "config/secrets.yaml",
        "oauth/client_secret.json",
        "gcloud/application_default_credentials.json",
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


def test_manager_rendering_prompt_keeps_presentation_out_of_engineer(tmp_path) -> None:
    apply_live_view_decision(
        tmp_path,
        decided=True,
        view=LiveViewDecision(
            title="赤壁赋",
            paths=("chibifu.md",),
            reason="Render the requested composition in the side panel.",
        ),
    )

    prompt = manager_rendering_prompt(tmp_path)

    assert "MANAGER ownership" in prompt
    assert "Do not assign" in prompt
    assert "Engineer" in prompt
    assert "chibifu.md" in prompt
    assert ".argus/live/" in prompt


def test_stage_response_can_select_manager_owned_rendering() -> None:
    decided, view = parse_live_view_response(json.dumps({
        "action": "hold",
        "target_stage": "draft",
        "reason": "more work",
        "live_view": {
            "title": "Current draft",
            "reason": "Manager-polished presentation",
            "paths": [".argus/live/current.md"],
        },
    }))

    assert decided is True
    assert view is not None
    assert view.paths == (".argus/live/current.md",)


def test_manager_presentation_is_written_by_confined_harness(tmp_path) -> None:
    raw = json.dumps({
        "live_view": {
            "title": "Manager view",
            "reason": "Polished for the operator",
            "paths": [".argus/live/current.md"],
        },
        "presentations": [{
            "path": ".argus/live/current.md",
            "content": "# Current result\n\nManager-authored presentation.\n",
        }],
    })

    view = apply_manager_rendering_response(tmp_path, raw)

    assert view is not None
    assert (tmp_path / ".argus" / "live" / "current.md").read_text(
        encoding="utf-8"
    ).startswith("# Current result")


def test_manager_presentation_refuses_symlinked_live_directory(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".argus").mkdir()
    (tmp_path / ".argus" / "live").symlink_to(outside, target_is_directory=True)
    raw = json.dumps({
        "live_view": {
            "title": "Unsafe",
            "reason": "Must not follow the live directory symlink.",
            "paths": [".argus/live/current.md"],
        },
        "presentations": [{
            "path": ".argus/live/current.md",
            "content": "must not escape",
        }],
    })

    with pytest.raises(ValueError, match="must not be a symlink"):
        apply_manager_rendering_response(tmp_path, raw)
    assert not (outside / "current.md").exists()


def test_manager_rendering_rejects_payload_atomically(tmp_path) -> None:
    live = tmp_path / ".argus" / "live"
    live.mkdir(parents=True)
    current = live / "current.md"
    current.write_text("old\n", encoding="utf-8")
    raw = json.dumps({
        "live_view": {
            "title": "Invalid",
            "reason": "Presentation is not selected.",
            "paths": ["other.md"],
        },
        "presentations": [{
            "path": ".argus/live/current.md",
            "content": "new\n",
        }],
    })

    with pytest.raises(ValueError, match="must be selected"):
        apply_manager_rendering_response(tmp_path, raw)
    assert current.read_text(encoding="utf-8") == "old\n"


def test_manager_clear_refuses_symlinked_argus_directory(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "live-view.json").write_text("keep\n", encoding="utf-8")
    (tmp_path / ".argus").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        apply_manager_rendering_response(
            tmp_path,
            json.dumps({"live_view": None, "presentations": []}),
        )
    assert (outside / "live-view.json").read_text(encoding="utf-8") == "keep\n"
