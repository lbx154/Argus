import subprocess
from pathlib import Path

import pytest

from argus.maintenance import publication


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def reviewed(tmp_path):
    source, origin = tmp_path / 'source', tmp_path / 'origin.git'
    source.mkdir()
    git(source, 'init', '--bare', '-q', str(origin))
    git(source, 'init', '-q')
    git(source, 'checkout', '-qb', 'main')
    git(source, 'config', 'user.name', 'Test')
    git(source, 'config', 'user.email', 'test@example.invalid')
    (source / 'implementation.txt').write_text('old behavior\n')
    git(source, 'add', 'implementation.txt')
    git(source, 'commit', '-qm', 'base')
    base = git(source, 'rev-parse', 'HEAD')
    git(source, 'remote', 'add', 'origin', str(origin))
    git(source, 'push', '-q', 'origin', 'main')
    (source / 'implementation.txt').write_text('reviewed behavior\n')
    git(source, 'add', 'implementation.txt')
    git(source, 'commit', '-qm', 'reviewed change')
    return source, origin, base, git(source, 'rev-parse', 'HEAD'), tmp_path / 'receipts'


def test_publication_uses_only_git_and_a_single_origin(reviewed, monkeypatch):
    source, origin, _base, candidate, receipts = reviewed
    # Authoring can continue; publication must use the frozen commit.
    (source / 'implementation.txt').write_text('later unreviewed edits\n')
    commands = []
    run = subprocess.run

    def record(command, **kwargs):
        commands.append(command)
        assert command[0] == 'git', 'publication must not run pytest/npm/release commands'
        return run(command, **kwargs)

    monkeypatch.setattr(publication.subprocess, 'run', record)
    result = publication.publish_reviewed_change(source, candidate, receipts)
    assert result['verdict'] == 'ADOPT'
    runtime = Path(result['runtime_source_root'])
    assert runtime != source
    assert (runtime / 'implementation.txt').read_text() == 'reviewed behavior\n'
    assert (source / 'implementation.txt').read_text() == 'later unreviewed edits\n'
    assert git(origin, 'rev-parse', 'main') == candidate
    assert git(source, 'remote') == 'origin'
    assert len([command for command in commands if command[1] == 'push']) == 1
    assert not any('private' in command or 'sync-public' in str(command) for command in commands)


def test_retry_reuses_source_without_rewinding_a_newer_origin_main(reviewed, monkeypatch):
    source, origin, _base, candidate, receipts = reviewed
    first = publication.publish_reviewed_change(source, candidate, receipts)
    (source / 'another.txt').write_text('later change\n')
    git(source, 'add', 'another.txt')
    git(source, 'commit', '-qm', 'later main')
    git(source, 'push', '-q', 'origin', 'main')
    newer = git(origin, 'rev-parse', 'main')
    original = publication._git

    def no_push(root, *args, **kwargs):
        assert args[0] != 'push', 'the candidate is already published'
        return original(root, *args, **kwargs)

    monkeypatch.setattr(publication, '_git', no_push)
    second = publication.publish_reviewed_change(source, candidate, receipts)
    assert second == first
    assert git(origin, 'rev-parse', 'main') == newer
    assert len(list((receipts / 'deployed-runtimes').iterdir())) == 1


def test_divergent_remote_is_not_overwritten(reviewed, tmp_path):
    source, origin, base, candidate, receipts = reviewed
    concurrent = tmp_path / 'concurrent'
    git(source, 'worktree', 'add', '--detach', str(concurrent), base)
    (concurrent / 'another.txt').write_text('other user change\n')
    git(concurrent, 'add', 'another.txt')
    git(concurrent, 'commit', '-qm', 'concurrent main')
    git(concurrent, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
    newer = git(origin, 'rev-parse', 'main')
    with pytest.raises(RuntimeError, match='git push failed'):
        publication.publish_reviewed_change(source, candidate, receipts)
    assert git(origin, 'rev-parse', 'main') == newer
    assert git(source, 'rev-parse', 'HEAD') == candidate


def test_changed_prepared_runtime_is_preserved_and_not_adopted(reviewed):
    source, origin, _base, candidate, receipts = reviewed
    first = publication.publish_reviewed_change(source, candidate, receipts)
    runtime = Path(first['runtime_source_root'])
    (runtime / 'implementation.txt').write_text('unreviewed edit in runtime\n')
    with pytest.raises(ValueError, match='no longer matches'):
        publication.publish_reviewed_change(source, candidate, receipts)
    assert (runtime / 'implementation.txt').read_text() == 'unreviewed edit in runtime\n'
    assert git(origin, 'rev-parse', 'main') == candidate
