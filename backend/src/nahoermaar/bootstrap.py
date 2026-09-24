# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""The application's single composition root."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .observability import configure_logging

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Application:
    """Dependencies owned by one application process."""

    settings: Settings


def bootstrap(
    environ: Mapping[str, str] | None = None,
    *,
    dotenv_path: Path = Path(".env"),
) -> Application:
    """Load configuration and compose the application process."""
    settings = Settings.load(environ, dotenv_path=dotenv_path)
    configure_logging(settings.log_level)
    _LOGGER.info("application.configured")
    return Application(settings)
