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
