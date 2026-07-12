from __future__ import annotations

import gzip
from pathlib import Path
from threading import Barrier, Lock, Thread

from argus_skill.core.cold_storage import (
    compact_skill_histories,
    compact_wiki_retired,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_skill_history_compression_is_lossless_and_idempotent(tmp_path: Path) -> None:
    history = tmp_path / "skills" / "_history" / "skill-1"
    for version in range(1, 6):
        _write(history / f"v{version}.md", f"version {version}\n")

    compressed = compact_skill_histories(tmp_path / "skills", keep_hot=2)

    assert {path.name for path in compressed} == {"v1.md.gz", "v2.md.gz", "v3.md.gz"}
    assert {path.name for path in history.glob("*.md")} == {"v4.md", "v5.md"}
    for version in range(1, 4):
        assert gzip.decompress((history / f"v{version}.md.gz").read_bytes()) == (
            f"version {version}\n".encode()
        )
    assert compact_skill_histories(tmp_path / "skills", keep_hot=2) == []


def test_wiki_retired_compression_groups_each_page_independently(tmp_path: Path) -> None:
    retired = tmp_path / "wiki" / "pages" / "_retired" / "techniques"
    for name in ("foo.md", "foo.2.md", "foo.3.md", "bar.md"):
        _write(retired / name, f"body {name}\n")

    compressed = compact_wiki_retired(tmp_path / "wiki", keep_hot=1)

    assert {path.name for path in compressed} == {"foo.md.gz", "foo.2.md.gz"}
    assert (retired / "foo.3.md").exists()
    assert (retired / "bar.md").exists()
    assert gzip.decompress((retired / "foo.md.gz").read_bytes()) == b"body foo.md\n"


def test_concurrent_cold_storage_passes_do_not_lose_history(tmp_path: Path) -> None:
    history = tmp_path / "skills" / "_history" / "skill-1"
    for version in range(1, 7):
        _write(history / f"v{version}.md", f"version {version}\n")
    barrier = Barrier(2)
    result_lock = Lock()
    results: list[Path] = []

    def compact() -> None:
        barrier.wait()
        rows = compact_skill_histories(tmp_path / "skills", keep_hot=2)
        with result_lock:
            results.extend(rows)

    threads = [Thread(target=compact) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert {path.name for path in results} == {
        "v1.md.gz",
        "v2.md.gz",
        "v3.md.gz",
        "v4.md.gz",
    }
    assert {path.name for path in history.glob("v*.md")} == {"v5.md", "v6.md"}
