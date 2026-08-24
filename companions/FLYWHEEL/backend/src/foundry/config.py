from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env(name: str, default: str) -> str:
    """Read only the Flywheel namespace to prevent rollback-data cross-writes."""
    return os.getenv(name, default)


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    data_dir: Path
    seed_data_dir: Path
    cors_origins: tuple[str, ...]
    poll_interval_seconds: float = 30.0
    auto_seed: bool = True
    event_retention_days: int = 90
    viewer_evaluator_command: tuple[str, ...] = ()
    viewer_evaluator_timeout_seconds: float = 600.0
    github_token_env: str = "GITHUB_TOKEN"
    release_git_timeout_seconds: float = 120.0
    # Flywheel never copies an LLM provider key.  It talks to the Argus control
    # plane and Argus keeps owning its backend/model/provider configuration.
    # The empty defaults keep explicitly constructed test settings isolated;
    # ``from_env`` enables the normal local companion pairing.
    argus_base_url: str = ""
    argus_token_env: str = "ARGUS_SKILL_WEB_TOKEN"
    # Compatibility-only switch for legacy API lifecycle tests that predate
    # immutable research provenance. This is intentionally not configurable by
    # ``from_env`` and therefore cannot become a production launch bypass.
    allow_unbound_campaign_launch_for_tests: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        foundry_dir = Path(__file__).resolve().parents[3]
        workspace_dir = Path(__file__).resolve().parents[4]
        bundled_seeds = foundry_dir / "data" / "seeds"
        development_seeds = workspace_dir / "research_roadmap" / "data"
        default_seeds = bundled_seeds if bundled_seeds.is_dir() else development_seeds
        origins = tuple(
            item.strip()
            for item in os.getenv(
                "FLYWHEEL_CORS_ORIGINS",
                "http://localhost:5175,http://127.0.0.1:5175",
            ).split(",")
            if item.strip()
        )
        return cls(
            database_path=Path(
                _env("FLYWHEEL_DATABASE_PATH", str(foundry_dir / "runtime" / "flywheel.db"))
            ).resolve(),
            data_dir=Path(
                _env("FLYWHEEL_DATA_DIR", str(foundry_dir / "runtime"))
            ).resolve(),
            seed_data_dir=Path(
                _env("FLYWHEEL_SEED_DATA_DIR", str(default_seeds))
            ).resolve(),
            cors_origins=origins,
            poll_interval_seconds=max(
                0.0, float(_env("FLYWHEEL_POLL_INTERVAL_SECONDS", "30"))
            ),
            auto_seed=_bool_env("FLYWHEEL_AUTO_SEED", True),
            event_retention_days=max(
                1, int(_env("FLYWHEEL_EVENT_RETENTION_DAYS", "90"))
            ),
            viewer_evaluator_command=cls._viewer_command(),
            viewer_evaluator_timeout_seconds=max(
                1.0, float(_env("FLYWHEEL_VIEWER_TIMEOUT_SECONDS", "600"))
            ),
            github_token_env=_env("FLYWHEEL_GITHUB_TOKEN_ENV", "GITHUB_TOKEN").strip()
            or "GITHUB_TOKEN",
            release_git_timeout_seconds=max(
                1.0, float(_env("FLYWHEEL_RELEASE_GIT_TIMEOUT_SECONDS", "120"))
            ),
            argus_base_url=_env(
                "FLYWHEEL_ARGUS_BASE_URL", "http://127.0.0.1:8799"
            ).strip().rstrip("/"),
            argus_token_env=_env(
                "FLYWHEEL_ARGUS_TOKEN_ENV", "ARGUS_SKILL_WEB_TOKEN"
            ).strip()
            or "ARGUS_SKILL_WEB_TOKEN",
        )

    @staticmethod
    def _viewer_command() -> tuple[str, ...]:
        raw = _env("FLYWHEEL_VIEWER_COMMAND_JSON", "").strip()
        if not raw:
            return ()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("FLYWHEEL_VIEWER_COMMAND_JSON must be valid JSON") from exc
        if not isinstance(value, list) or not value or not all(
            isinstance(part, str) and part for part in value
        ):
            raise ValueError("FLYWHEEL_VIEWER_COMMAND_JSON must be a non-empty JSON string array")
        return tuple(value)
