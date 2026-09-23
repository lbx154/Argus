import io
import json

from argus.tools import web_search

RSS = b'<rss><channel><item><title>New model</title><link>https://publisher.test/model</link><description>Official introduction</description></item></channel></rss>'


def test_search_saves_real_results_and_reuses_a_recent_snapshot(tmp_path, monkeypatch):
    calls = []

    def request(req, timeout):
        calls.append(req.full_url)
        assert timeout == 12
        return io.BytesIO(RSS)

    monkeypatch.setattr(web_search, "urlopen", request)
    result = web_search.search_web("Jev", tmp_path)
    assert result["status"] == "ok" and not result["cached"]
    assert result["results"][0]["url"] == "https://publisher.test/model"
    assert json.loads((tmp_path / ".argus/sources" / result["path"].split("/")[-1]).read_text())["query"] == "Jev"
    assert web_search.search_web("Jev", tmp_path)["cached"]
    assert len(calls) == 1
    web_search.search_web("Jev", tmp_path, refresh=True)
    assert len(calls) == 2


def test_200_html_challenge_is_not_a_successful_search(tmp_path, monkeypatch):
    monkeypatch.setattr(web_search, "urlopen", lambda *a, **k: io.BytesIO(b'<html>Verify you are human</html>'))
    result = web_search.search_web("unknown model", tmp_path)
    assert result["status"] == "unavailable" and result["results"] == []
    assert not (tmp_path / ".argus/sources").exists()


def test_no_matches_and_transport_failure_remain_distinct(tmp_path, monkeypatch):
    monkeypatch.setattr(web_search, "urlopen", lambda *a, **k: io.BytesIO(b'<rss><channel/></rss>'))
    assert web_search.search_web("unseen model", tmp_path)["status"] == "no_results"

    def offline(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(web_search, "urlopen", offline)
    # A previous empty result is not a durable claim of nonexistence.
    assert web_search.search_web("unseen model", tmp_path)["status"] == "unavailable"


def test_stale_results_are_refreshed_and_search_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(web_search, "urlopen", lambda *a, **k: io.BytesIO(RSS))
    first = web_search.search_web("Jev", tmp_path)
    monkeypatch.setattr(web_search.time, "time", lambda: first["accessed_at"] + web_search.CACHE_SECONDS + 1)
    assert not web_search.search_web("Jev", tmp_path)["cached"]
    monkeypatch.setattr(web_search, "urlopen", lambda *a, **k: io.BytesIO(b'x' * (web_search.MAX_RESPONSE_BYTES + 1)))
    assert web_search.search_web("different", tmp_path)["status"] == "unavailable"
