from __future__ import annotations

import hashlib
import io
import urllib.error

import pytest

from argus_skill.trial import native_cli


class Response(io.BytesIO):
    def __init__(self, content, length):
        super().__init__(content)
        self.headers = {"Content-Length": length}


@pytest.mark.parametrize("length", ["131072", "", "invalid"])
def test_download_reports_real_bytes_and_optional_total(tmp_path, monkeypatch, length):
    content = b"a" * 131072
    monkeypatch.setattr(native_cli.urllib.request, "urlopen", lambda *a, **k: Response(content, length))
    reports = []
    archive = tmp_path / "fixture.zip"
    native_cli._download_archive(object(), archive, hashlib.sha256(content).hexdigest(),
                                 lambda count, total: reports.append((count, total)))
    assert reports[0][0] == 0 and reports[-1][0] == len(content)
    assert reports[-1][1] == (len(content) if length.isdigit() else None)
    assert archive.read_bytes() == content


def test_network_failure_is_explained_without_automatic_retry(tmp_path, monkeypatch):
    calls = []
    def failed(*args, **kwargs):
        calls.append(1)
        raise urllib.error.URLError("synthetic timeout")
    monkeypatch.setattr(native_cli.urllib.request, "urlopen", failed)
    with pytest.raises(ValueError, match="不会自动重复下载"):
        native_cli._download_archive(object(), tmp_path / "fixture.zip", "unused")
    assert calls == [1]


def test_progress_does_not_replace_checksum_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(native_cli.urllib.request, "urlopen", lambda *a, **k: Response(b"bad", "3"))
    with pytest.raises(ValueError, match="校验失败"):
        native_cli._download_archive(object(), tmp_path / "fixture.zip", "0" * 64, lambda *a: None)
