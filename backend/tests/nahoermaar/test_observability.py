# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from uuid import uuid4

from nahoermaar.config import LogLevel
from nahoermaar.observability import (
    ContextFilter,
    LogContext,
    configure_logging,
    log_context,
    safe_log_value,
    sanitize_log_message,
)
from nahoermaar.operations.logs import RecentLogBuffer


def test_recent_logs_are_bounded_sanitized_and_correlated() -> None:
    logs = RecentLogBuffer(capacity=2)
    logs.addFilter(ContextFilter())
    logger = logging.getLogger("nahoermaar.test.observability")
    logger.handlers = [logs]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    correlation_id = uuid4()
    actor_id = uuid4()

    with log_context(
        LogContext(
            request_id="request-1",
            correlation_id=correlation_id,
            actor_id=actor_id,
        )
    ):
        logger.info("first line\nsecond line")
        logger.info("second")
        logger.info("third", extra={"actor_id": "-"})

    entries = logs.entries()
    assert [entry.message for entry in entries] == ["second", "third"]
    assert entries[0].source == "test.observability"
    assert entries[0].request_id == "request-1"
    assert entries[0].correlation_id == correlation_id
    assert entries[0].actor_id == actor_id
    assert entries[1].actor_id == actor_id
    assert logs.entries(after=entries[0].id) == (entries[1],)
    assert safe_log_value("hello\r\nworld", limit=20) == "hello world"
    assert (
        sanitize_log_message(
            "GET https://example.test/callback?state=secret&code=value"
        )
        == "GET https://example.test/callback"
    )
    assert sanitize_log_message("token=secret") == "token=<redacted>"


def test_logging_configuration_uses_daily_rotation_and_retention(
    tmp_path: Path,
) -> None:
    root = logging.getLogger()
    existing_handlers = tuple(root.handlers)
    existing_level = root.level
    logs = configure_logging(LogLevel.DEBUG, tmp_path, 9)
    try:
        added_handlers = tuple(
            handler for handler in root.handlers if handler not in existing_handlers
        )
        rotating = next(
            handler
            for handler in added_handlers
            if isinstance(handler, TimedRotatingFileHandler)
        )
        assert logs in added_handlers
        assert rotating.backupCount == 9
        assert Path(rotating.baseFilename) == tmp_path / "nahoermaar.log"
        logging.getLogger("nahoermaar.test").info("configured")
        assert (tmp_path / "nahoermaar.log").is_file()
    finally:
        for handler in tuple(root.handlers):
            if handler not in existing_handlers:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(existing_level)
