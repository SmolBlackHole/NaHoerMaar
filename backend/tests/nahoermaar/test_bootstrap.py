# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from nahoermaar.bootstrap import bootstrap
from nahoermaar.config import LogLevel


def test_bootstrap_loads_settings_and_configures_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured: list[LogLevel] = []
    monkeypatch.setattr("nahoermaar.bootstrap.configure_logging", configured.append)

    application = bootstrap(
        {
            "DATABASE_URL": "postgresql+psycopg://app:secret@database/nahoermaar",
            "LOG_LEVEL": "WARNING",
        }
    )

    assert application.settings.log_level is LogLevel.WARNING
    assert configured == [LogLevel.WARNING]
