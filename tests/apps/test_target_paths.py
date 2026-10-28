from argus.apps._target_paths import resolve_life_root


def test_a_project_state_directory_resolves_to_its_global_root(tmp_path) -> None:
    from argus.core.project import project_fingerprint

    projects = tmp_path / "home" / "projects"
    escaped = projects / project_fingerprint(tmp_path / "work").fingerprint
    escaped.mkdir(parents=True)
    assert resolve_life_root(escaped) == tmp_path / "home"
    # A global root that merely sits under a folder called "projects" stays as is.
    plain = projects / "argus-home"
    plain.mkdir()
    assert resolve_life_root(plain) == plain
