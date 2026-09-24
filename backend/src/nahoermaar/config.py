# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Application settings loaded once at the process boundary."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

CALLBACK_PATH = "/api/auth/discord/callback"
_DISCORD_ID = re.compile(r"[1-9][0-9]{0,19}")


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
class AuthSettings:
    """Canonical browser and Discord OAuth configuration."""

    public_origin: str
    client_id: str
    client_secret: str = field(repr=False)
    access_path: Path

    def __post_init__(self) -> None:
        parsed = urlsplit(self.public_origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or self.public_origin != f"{parsed.scheme}://{parsed.netloc}"
            or (
                parsed.scheme == "http"
                and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            )
        ):
            raise ConfigurationError(
                "PUBLIC_ORIGIN must be an HTTPS origin "
                "(HTTP is allowed on loopback only)."
            )
        if self.client_id and (
            _DISCORD_ID.fullmatch(self.client_id) is None
            or int(self.client_id) >= 2**64
        ):
            raise ConfigurationError(
                "DISCORD_CLIENT_ID must be a Discord application ID."
            )

    @property
    def secure(self) -> bool:
        """Return whether authentication cookies require HTTPS."""
        return self.public_origin.startswith("https://")

    @property
    def redirect_uri(self) -> str:
        """Return the fixed Discord callback registered for this deployment."""
        return self.public_origin + CALLBACK_PATH


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration currently required by the application."""

    database_url: str
    auth: AuthSettings
    log_level: LogLevel = LogLevel.INFO
    node_path: Path = Path("node")

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
        origin = values.get("PUBLIC_ORIGIN", "http://localhost:3000").strip()
        if origin.endswith("/"):
            origin = origin[:-1]
        auth = AuthSettings(
            public_origin=origin,
            client_id=values.get("DISCORD_CLIENT_ID", "").strip(),
            client_secret=values.get("DISCORD_CLIENT_SECRET", "").strip(),
            access_path=Path(
                values.get("ACCESS_PATH") or "config/access.toml"
            ).resolve(),
        )
        return cls(
            database_url,
            auth,
            log_level,
            Path(values.get("NODE_PATH") or "node"),
        )
