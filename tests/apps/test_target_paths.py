from argus_skill.apps._target_paths import resolve_life_root


def test_relative_life_dir_is_anchored_before_daemon_detaches(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert resolve_life_root("state") == tmp_path / "state"
