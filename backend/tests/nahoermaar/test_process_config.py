# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from pathlib import Path

import pytest

from nahoermaar.config import ConfigurationError, LogLevel, Settings


def test_settings_load_explicit_environment() -> None:
    settings = Settings.load(
        {
            "DATABASE_URL": "postgresql+psycopg://app:secret@database/nahoermaar",
            "LOG_LEVEL": "debug",
            "DISCORD_CLIENT_ID": "1550894913980465212",
            "DISCORD_CLIENT_SECRET": "local-secret",
            "PUBLIC_ORIGIN": "https://music.example.test",
            "ACCESS_PATH": "config/access.toml",
            "NAHORMAAR_LOG_DIR": "var/logs",
            "LOG_RETENTION_DAYS": "21",
        }
    )

    assert settings.database_url.endswith("@database/nahoermaar")
    assert settings.log_level is LogLevel.DEBUG
    assert settings.auth.client_id == "1550894913980465212"
    assert settings.auth.redirect_uri == (
        "https://music.example.test/api/auth/discord/callback"
    )
    assert settings.auth.secure
    assert settings.log_directory == (Path.cwd() / "var/logs").resolve()
    assert settings.log_retention_days == 21


def test_process_environment_overrides_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "DATABASE_URL=postgresql+psycopg://file/database\nLOG_LEVEL=WARNING\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://process/database")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    settings = Settings.load(dotenv_path=dotenv_path)

    assert settings.database_url == "postgresql+psycopg://process/database"
    assert settings.log_level is LogLevel.ERROR


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({}, "DATABASE_URL"),
        ({"DATABASE_URL": "postgres", "LOG_LEVEL": "verbose"}, "LOG_LEVEL"),
        (
            {"DATABASE_URL": "postgres", "LOG_RETENTION_DAYS": "forever"},
            "LOG_RETENTION_DAYS",
        ),
        (
            {"DATABASE_URL": "postgres", "LOG_RETENTION_DAYS": "0"},
            "LOG_RETENTION_DAYS",
        ),
        (
            {
                "DATABASE_URL": "postgres",
                "PUBLIC_ORIGIN": "http://music.example.test",
            },
            "PUBLIC_ORIGIN",
        ),
        (
            {
                "DATABASE_URL": "postgres",
                "DISCORD_CLIENT_ID": "not-a-snowflake",
            },
            "DISCORD_CLIENT_ID",
        ),
    ],
)
def test_settings_reject_invalid_values(
    environment: dict[str, str], message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        Settings.load(environment)
