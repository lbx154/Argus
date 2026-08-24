from __future__ import annotations

from pathlib import Path

from foundry.db import Database, decode_row
from foundry.managed_argus import (
    MANAGED_ARGUS_CONNECTION_ID,
    ensure_managed_argus_connection,
)


def test_managed_argus_pairing_delegates_provider_configuration(tmp_path: Path) -> None:
    database = Database(tmp_path / "flywheel.db")
    database.migrate()

    result = ensure_managed_argus_connection(
        database,
        base_url="http://127.0.0.1:8799/",
        token_env="ARGUS_SKILL_WEB_TOKEN",
    )

    assert result == MANAGED_ARGUS_CONNECTION_ID
    row = decode_row(
        database.fetch_one(
            "SELECT * FROM connections WHERE id=?", (MANAGED_ARGUS_CONNECTION_ID,)
        )
    )
    assert row is not None
    assert row["base_url"] == "http://127.0.0.1:8799"
    assert row["token_ref"] == "env:ARGUS_SKILL_WEB_TOKEN"
    assert row["status"] == "unknown"
    assert row["metadata"]["provider_configuration"] == "delegated_to_argus"
    assert row["metadata"]["model_configuration"] == "delegated_to_argus"
    assert row["metadata"]["llm_credentials_copied"] is False

    database.execute(
        "UPDATE connections SET status='online',metadata_json=? WHERE id=?",
        (
            '{"launch_compatible":true,"argus_revision":"0123456789abcdef0123456789abcdef01234567"}',
            MANAGED_ARGUS_CONNECTION_ID,
        ),
    )
    ensure_managed_argus_connection(
        database,
        base_url="http://127.0.0.1:8799",
        token_env="ARGUS_SKILL_WEB_TOKEN",
    )
    preserved = decode_row(
        database.fetch_one(
            "SELECT * FROM connections WHERE id=?", (MANAGED_ARGUS_CONNECTION_ID,)
        )
    )
    assert preserved is not None
    assert preserved["status"] == "online"
    assert preserved["metadata"]["launch_compatible"] is True
    assert preserved["metadata"]["managed_by"] == "argus-flywheel"


def test_managed_argus_pairing_is_disabled_when_endpoint_is_blank(tmp_path: Path) -> None:
    database = Database(tmp_path / "flywheel.db")
    database.migrate()

    assert (
        ensure_managed_argus_connection(
            database, base_url="  ", token_env="ARGUS_SKILL_WEB_TOKEN"
        )
        is None
    )
    assert database.fetch_one("SELECT * FROM connections") is None
