from __future__ import annotations

import json
import threading

import pytest

from argus_skill.manager.live_view import (
    LIVE_VIEW_MANIFEST,
    LiveViewDecision,
    apply_live_view_decision,
    apply_manager_rendering_response,
    load_live_view_decision,
    normalize_live_view_path,
    parse_live_view_response,
    parse_manager_presentations,
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


def test_live_view_manifest_can_be_session_scoped(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    workspace.mkdir()
    state.mkdir()
    view = LiveViewDecision(
        title="Session view",
        paths=("research/PROGRESS.md",),
        reason="Belongs only to this session.",
    )

    apply_live_view_decision(
        workspace,
        decided=True,
        view=view,
        manifest_root=state,
    )

    assert not (workspace / LIVE_VIEW_MANIFEST).exists()
    assert (state / LIVE_VIEW_MANIFEST).exists()
    assert load_live_view_decision(workspace) is None
    assert load_live_view_decision(workspace, manifest_root=state) == view


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


def test_stage_null_live_view_preserves_last_valid_view(tmp_path) -> None:
    previous = LiveViewDecision(
        title="Current proof",
        paths=(".argus/live/current.md",),
        reason="Keep this visible.",
    )
    apply_live_view_decision(tmp_path, decided=True, view=previous)

    view = apply_manager_rendering_response(
        tmp_path,
        json.dumps({"live_view": None, "presentations": []}),
    )

    assert view == previous
    assert load_live_view_decision(tmp_path) == previous


def test_explicit_manager_clear_removes_last_valid_view(tmp_path) -> None:
    apply_live_view_decision(
        tmp_path,
        decided=True,
        view=LiveViewDecision(
            title="Old view",
            paths=("research/OLD.md",),
            reason="No longer relevant.",
        ),
    )

    view = apply_manager_rendering_response(
        tmp_path,
        json.dumps({
            "live_view": None,
            "clear_live_view": True,
            "presentations": [],
        }),
    )

    assert view is None
    assert load_live_view_decision(tmp_path) is None


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


def test_manager_rendering_does_not_reenter_wiki_lock(tmp_path) -> None:
    fcntl = pytest.importorskip("fcntl", reason="requires POSIX advisory locking")
    wiki_lock = tmp_path / ".autors" / "demo" / "wiki" / "data" / ".wiki.lock"
    wiki_lock.parent.mkdir(parents=True)
    raw = json.dumps({
        "live_view": {
            "title": "Manager view",
            "reason": "Must remain independent of wiki maintenance.",
            "paths": [".argus/live/current.md"],
        },
        "presentations": [{
            "path": ".argus/live/current.md",
            "content": "# Current result\n",
        }],
    })
    completed = threading.Event()
    errors: list[BaseException] = []

    def render() -> None:
        try:
            apply_manager_rendering_response(tmp_path, raw)
        except BaseException as exc:
            errors.append(exc)
        finally:
            completed.set()

    with wiki_lock.open("a+b") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        worker = threading.Thread(target=render, daemon=True)
        worker.start()
        finished_while_locked = completed.wait(timeout=2.0)
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)

    worker.join(timeout=2.0)
    assert finished_while_locked, "Manager rendering waited on the wiki lock"
    assert not errors
    assert not worker.is_alive()


def test_missing_manager_markdown_presentation_gets_truthful_fallback(tmp_path) -> None:
    raw = json.dumps({
        "action": "advance",
        "target_stage": "solve",
        "reason": "Scope evidence was accepted.",
        "live_view": {
            "title": "Erdos proof progress",
            "reason": "Show the current stage and decision.",
            "paths": [".argus/live/erdos-proof-progress.md"],
        },
    })

    view = apply_manager_rendering_response(tmp_path, raw)

    assert view is not None
    content = (tmp_path / ".argus/live/erdos-proof-progress.md").read_text(
        encoding="utf-8"
    )
    assert "# Erdos proof progress" in content
    assert "`advance`" in content
    assert "`solve`" in content
    assert "Scope evidence was accepted." in content


def test_missing_manager_presentation_replaces_stale_status_page(tmp_path) -> None:
    target = tmp_path / ".argus/live/current.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Stale\n", encoding="utf-8")
    raw = json.dumps({
        "action": "hold",
        "target_stage": "solve",
        "reason": "A new obstruction is under review.",
        "live_view": {
            "title": "Current proof status",
            "reason": "Fresh status for this round.",
            "paths": [".argus/live/current.md"],
        },
    })

    apply_manager_rendering_response(tmp_path, raw)

    content = target.read_text(encoding="utf-8")
    assert "# Stale" not in content
    assert "# Current proof status" in content
    assert "A new obstruction is under review." in content


def test_manager_live_view_rejects_missing_state_relative_paths(tmp_path) -> None:
    old = tmp_path / "research" / "OLD.md"
    old.parent.mkdir(parents=True)
    old.write_text("# Old valid view\n", encoding="utf-8")
    previous = LiveViewDecision(
        title="Previous",
        paths=("research/OLD.md",),
        reason="Keep the last materialized view.",
    )
    apply_live_view_decision(tmp_path, decided=True, view=previous)
    raw = json.dumps({
        "live_view": {
            "title": "Broken",
            "reason": "Files exist only in the state directory.",
            "paths": [
                "manager_live/ENVELOPE_GAP_PROOF_ZH.pdf",
                "manager_live/ENVELOPE_GAP_PROOF_ZH.md",
            ],
        },
        "presentations": [],
    })

    with pytest.raises(ValueError, match="no materialized artifact"):
        apply_manager_rendering_response(tmp_path, raw)

    assert load_live_view_decision(tmp_path) == previous


def test_manager_live_view_accepts_existing_workspace_pdf(tmp_path) -> None:
    pdf = tmp_path / "research" / "proof.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\n")
    raw = json.dumps({
        "live_view": {
            "title": "Proof",
            "reason": "Existing canonical workspace PDF.",
            "paths": ["research/proof.pdf"],
        },
        "presentations": [],
    })

    view = apply_manager_rendering_response(tmp_path, raw)

    assert view is not None
    assert view.paths == ("research/proof.pdf",)


@pytest.mark.parametrize("suffix", ["md", "markdown", "html", "json", "csv", "tsv", "txt"])
def test_manager_can_author_supported_presentation_formats(
    tmp_path, suffix: str,
) -> None:
    path = f".argus/live/current.{suffix}"
    raw = json.dumps({
        "live_view": {
            "title": "Manager-created view",
            "reason": "Best operator-facing representation",
            "paths": [path],
        },
        "presentations": [{"path": path, "content": "content"}],
    })

    apply_manager_rendering_response(tmp_path, raw)

    assert (tmp_path / path).read_text(encoding="utf-8") == "content"


def test_manager_presentation_refuses_symlinked_live_directory(
    tmp_path,
    require_symlink_support,
) -> None:
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


def test_manager_clear_refuses_symlinked_argus_directory(
    tmp_path,
    require_symlink_support,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "live-view.json").write_text("keep\n", encoding="utf-8")
    (tmp_path / ".argus").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        apply_manager_rendering_response(
            tmp_path,
            json.dumps({
                "live_view": None,
                "clear_live_view": True,
                "presentations": [],
            }),
        )
    assert (outside / "live-view.json").read_text(encoding="utf-8") == "keep\n"
