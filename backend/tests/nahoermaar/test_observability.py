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
    causation_id = uuid4()
    actor_id = uuid4()

    with log_context(
        LogContext(
            request_id="request-1",
            correlation_id=correlation_id,
            causation_id=causation_id,
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
    assert entries[0].causation_id == causation_id
    assert entries[0].actor_id == actor_id
    assert entries[1].actor_id == actor_id
    assert logs.entries(after=entries[0].id) == (entries[1],)
    assert logs.entries(query="SECOND") == (entries[0],)
    assert logs.entries(level="info", source="test.observability") == entries
    assert logs.entries(actor_id=actor_id, request_id="request-1") == entries
    assert logs.entries(correlation_id=correlation_id) == entries
    assert logs.entries(causation_id=uuid4()) == ()
    assert logs.entries(query="second", limit=1) == (entries[0],)
    assert safe_log_value("hello\r\nworld", limit=20) == "hello world"
    assert (
        sanitize_log_message(
            "GET https://example.test/callback?state=secret&code=value"
        )
        == "GET https://example.test/callback"
    )
    assert sanitize_log_message("token=secret") == "token=<redacted>"


def test_recent_logs_keep_latest_window_and_drain_cursor_backlog() -> None:
    logs = RecentLogBuffer(capacity=6)
    logger = logging.getLogger("nahoermaar.test.log_backlog")
    logger.handlers = [logs]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    for index in range(6):
        logger.info("entry-%s", index)

    assert [entry.message for entry in logs.entries(limit=2)] == [
        "entry-4",
        "entry-5",
    ]
    first = logs.entries(after=0, limit=2)
    second = logs.entries(after=first[-1].id, limit=2)
    third = logs.entries(after=second[-1].id, limit=2)
    assert [entry.message for entry in (*first, *second, *third)] == [
        "entry-0",
        "entry-1",
        "entry-2",
        "entry-3",
        "entry-4",
        "entry-5",
    ]


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
