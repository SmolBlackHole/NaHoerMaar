# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Application settings loaded once at the process boundary."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from dotenv import dotenv_values


class ConfigurationError(ValueError):
    """Raised when process configuration is incomplete or invalid."""


class LogLevel(StrEnum):
    """Supported application log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def environment_values(
    environ: Mapping[str, str] | None = None,
    *,
    dotenv_path: Path = Path(".env"),
) -> Mapping[str, str]:
    """Return an explicit environment or `.env` overlaid by the process."""
    if environ is not None:
        return dict(environ)
    file_values = {
        key: value
        for key, value in dotenv_values(dotenv_path).items()
        if value is not None
    }
    return file_values | dict(os.environ)


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration currently required by the application."""

    database_url: str
    log_level: LogLevel = LogLevel.INFO

    @classmethod
    def load(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        dotenv_path: Path = Path(".env"),
    ) -> "Settings":
        """Load and validate settings without changing the environment."""
        values = environment_values(environ, dotenv_path=dotenv_path)
        database_url = values.get("DATABASE_URL", "").strip()
        if not database_url:
            raise ConfigurationError("Set DATABASE_URL in the environment.")
        try:
            log_level = LogLevel(values.get("LOG_LEVEL", "INFO").strip().upper())
        except ValueError as error:
            raise ConfigurationError(
                "LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL."
            ) from error
        return cls(database_url, log_level)
