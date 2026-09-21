from __future__ import annotations

from argus.trial import web_portal as portal
from argus.trial.analytics import Analytics


def test_the_portal_lets_a_trial_ask_what_its_map_lines_say():
    assert portal.permitted("/api/map-lines/project/s-1234", "POST")
    assert portal.permitted("/api/map-lines/dataset/demo", "POST")
    assert not portal.permitted("/api/map-lines/project/s-1234/extra", "POST")
    assert portal.permitted("/api/map-cards/project/s-1234", "POST")
    assert portal.permitted("/api/map-question-source/project/s-1234", "POST")
    assert not portal.permitted("/api/map-question-source/project/s-1234/extra", "POST")


def test_analytics_keeps_the_project_name_out_of_the_map_lines_path():
    assert Analytics._route("/api/map-lines/project/PRIVATE-NAME") == "/api/map-lines/project/:name"
    assert Analytics._route("/api/map-copy/dataset/PRIVATE") == "/api/map-copy/dataset/:name"
    assert Analytics._route("/api/map-cards/project/PRIVATE-NAME") == "/api/map-cards/project/:name"
    assert Analytics._route("/api/map-question-source/project/PRIVATE-NAME?session_id=PRIVATE") == "/api/map-question-source/project/:name"
