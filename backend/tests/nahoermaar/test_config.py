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
        }
    )

    assert settings.database_url.endswith("@database/nahoermaar")
    assert settings.log_level is LogLevel.DEBUG


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
    ],
)
def test_settings_reject_invalid_values(
    environment: dict[str, str], message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        Settings.load(environment)
