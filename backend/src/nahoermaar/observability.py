# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Process-wide observability configuration."""

from logging.config import dictConfig

from .config import LogLevel


def configure_logging(level: LogLevel) -> None:
    """Configure one predictable console logger for the process."""
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "console": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "console",
                    "stream": "ext://sys.stderr",
                }
            },
            "root": {"handlers": ["console"], "level": level.value},
        }
    )
