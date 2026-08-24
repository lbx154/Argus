from __future__ import annotations

from pathlib import Path

from foundry.config import Settings


def test_default_runtime_paths_are_isolated_from_seed_data(monkeypatch) -> None:
    for name in (
        "FLYWHEEL_DATABASE_PATH",
        "FLYWHEEL_DATA_DIR",
        "FLYWHEEL_SEED_DATA_DIR",
        "FOUNDRY_DATABASE_PATH",
        "FOUNDRY_DATA_DIR",
        "FOUNDRY_SEED_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.data_dir.name == "runtime"
    assert settings.database_path == settings.data_dir / "flywheel.db"
    assert settings.seed_data_dir.name == "seeds"
    assert settings.seed_data_dir.parent.name == "data"
    assert settings.seed_data_dir != settings.data_dir


def test_legacy_foundry_environment_cannot_redirect_flywheel_runtime(monkeypatch) -> None:
    monkeypatch.delenv("FLYWHEEL_DATABASE_PATH", raising=False)
    monkeypatch.delenv("FLYWHEEL_DATA_DIR", raising=False)
    monkeypatch.setenv("FOUNDRY_DATABASE_PATH", "C:/legacy/foundry.db")
    monkeypatch.setenv("FOUNDRY_DATA_DIR", "C:/legacy/runtime")

    settings = Settings.from_env()

    assert settings.database_path != Path("C:/legacy/foundry.db")
    assert settings.data_dir != Path("C:/legacy/runtime")
    assert settings.database_path.name == "flywheel.db"
