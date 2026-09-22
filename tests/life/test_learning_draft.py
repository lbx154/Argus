from pathlib import Path

import pytest

from argus.life.learning_draft import LearningDraft


def page(body: str, *, title: str = "Current finding") -> str:
    return f"---\ntitle: {title}\ndescription: {body}\n---\n\n{body}\n"


def setup(tmp_path: Path):
    root = tmp_path / "wiki" / "pages" / "surveys"
    root.mkdir(parents=True)
    current = root / "wal.md"
    current.write_text(page("Old finding"))
    return root, current


def test_unpublished_or_invalid_drafts_preserve_all_canonical_content(tmp_path):
    root, current = setup(tmp_path)
    skill_root = tmp_path / "skills"
    with LearningDraft({"knowledge": root, "skills": skill_root}, tmp_path / "drafts") as draft:
        (draft.paths["knowledge"] / current.name).write_text(page("Corrected finding"))
        assert current.read_text() == page("Old finding")
        # One valid page must not escape when another draft is incomplete.
        (draft.paths["skills"] / "broken.md").write_text("---\nname: incomplete\n")
        with pytest.raises(ValueError):
            draft.publish()
    assert current.read_text() == page("Old finding")
    assert not skill_root.exists()


def test_publication_keeps_history_out_of_the_current_page_and_next_draft(tmp_path):
    root, current = setup(tmp_path)
    with LearningDraft({"knowledge": root}, tmp_path / "drafts") as draft:
        (draft.paths["knowledge"] / current.name).write_text(page("Corrected finding"))
        assert draft.publish() == [current]
    assert current.read_text() == page("Corrected finding")
    assert next((root / ".history").glob("*.md")).read_text() == page("Old finding")
    with LearningDraft({"knowledge": root}, tmp_path / "drafts") as draft:
        assert not (draft.paths["knowledge"] / ".history").exists()
        assert draft.publish() == []


def test_concurrent_canonical_edit_is_not_overwritten(tmp_path):
    root, current = setup(tmp_path)
    with LearningDraft({"knowledge": root}, tmp_path / "drafts") as draft:
        (draft.paths["knowledge"] / current.name).write_text(page("Background finding"))
        current.write_text(page("Newer human correction"))
        with pytest.raises(ValueError, match="changed while drafting"):
            draft.publish()
    assert current.read_text() == page("Newer human correction")


def test_same_content_under_a_new_filename_is_not_new_learning(tmp_path):
    root, current = setup(tmp_path)
    with LearningDraft({"knowledge": root}, tmp_path / "drafts") as draft:
        (draft.paths["knowledge"] / "duplicate.md").write_text(page("Old finding", title="Rephrased title"))
        assert draft.publish() == []
    assert list(root.glob("*.md")) == [current]


def test_model_cannot_delete_a_page_or_publish_through_a_symlink(tmp_path):
    root, current = setup(tmp_path)
    with LearningDraft({"knowledge": root}, tmp_path / "drafts") as draft:
        (draft.paths["knowledge"] / current.name).unlink()
        with pytest.raises(ValueError, match="delete"):
            draft.publish()
    with LearningDraft({"knowledge": root}, tmp_path / "drafts") as draft:
        (draft.paths["knowledge"] / "alias.md").symlink_to(current)
        with pytest.raises((ValueError, OSError)):
            draft.publish()
    assert current.read_text() == page("Old finding")


def test_write_failure_rolls_back_previously_published_files(tmp_path, monkeypatch):
    from argus.life import learning_draft

    root, current = setup(tmp_path)
    second = root / "z.md"
    second.write_text(page("Second old finding"))
    replace = learning_draft._replace

    def fail_second(path, text):
        if path == second and text == page("Second new finding"):
            raise OSError("simulated disk write failure")
        replace(path, text)

    with LearningDraft({"knowledge": root}, tmp_path / "drafts") as draft:
        (draft.paths["knowledge"] / current.name).write_text(page("New finding"))
        (draft.paths["knowledge"] / second.name).write_text(page("Second new finding"))
        monkeypatch.setattr(learning_draft, "_replace", fail_second)
        with pytest.raises(OSError, match="disk write"):
            draft.publish()
    assert current.read_text() == page("Old finding")
    assert second.read_text() == page("Second old finding")
