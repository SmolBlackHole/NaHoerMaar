# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import logging
from pathlib import Path

from nahormaar_backend.engine.logs import (
    LOG_FILE,
    LOG_RETENTION_DAYS,
    AccessLogFilter,
    RecentLogs,
    current_actor,
    current_trace_id,
    log_context,
    logging_configuration,
    request_trace_id,
)


def test_recent_logs_capture_and_clear_actor_context() -> None:
    handler = RecentLogs()
    logger = logging.getLogger("nahormaar_backend.engine.test_context")
    logger.addHandler(handler)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        with log_context(
            "42",
            "Kai\nListener",
            trace_id="44d23529-2d7a-43f7-a4e7-ae25961dd224",
        ):
            assert current_trace_id() == "44d23529-2d7a-43f7-a4e7-ae25961dd224"
            assert current_actor() == ("42", "Kai Listener")
            logger.info("engine.test.with_actor")
        logger.info("engine.test.without_actor")
    finally:
        logger.setLevel(previous_level)
        logger.removeHandler(handler)

    actor, background = handler.since()
    assert actor.actor_id == "42"
    assert actor.actor_name == "Kai Listener"
    assert actor.trace_id == "44d23529-2d7a-43f7-a4e7-ae25961dd224"
    assert background.actor_id is None
    assert background.actor_name is None
    assert background.trace_id is None
    assert current_trace_id() is None
    assert current_actor() == (None, None)


def test_request_trace_id_accepts_only_canonical_uuid_values() -> None:
    expected = "44d23529-2d7a-43f7-a4e7-ae25961dd224"

    assert request_trace_id(expected.upper()) == expected
    assert request_trace_id("not-a-request-id") != "not-a-request-id"
    assert request_trace_id() != request_trace_id()


def test_logging_configuration_rotates_daily_and_keeps_two_weeks(
    tmp_path: Path,
) -> None:
    configuration = logging_configuration(tmp_path / "logs")
    handler = configuration["handlers"]["file"]

    assert handler["class"] == "logging.handlers.TimedRotatingFileHandler"
    assert handler["filename"] == str(tmp_path / "logs" / LOG_FILE)
    assert handler["when"] == "midnight"
    assert handler["backupCount"] == LOG_RETENTION_DAYS
    assert handler["utc"] is True
    assert handler["level"] == "DEBUG"
    assert configuration["handlers"]["default"]["level"] == "INFO"
    assert configuration["loggers"]["nahormaar_backend"]["level"] == "DEBUG"
    assert "trace_id=%(trace_id)s" in configuration["formatters"]["default"]["fmt"]


def test_access_log_filter_removes_all_query_values() -> None:
    for path, expected in (
        ("/api/auth/callback?code=secret&state=one-use", "/api/auth/callback"),
        ("/api/catalog/search?q=private+request", "/api/catalog/search"),
    ):
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:1234", "GET", path, "1.1", 200),
            None,
        )

        assert AccessLogFilter().filter(record) is True
        assert isinstance(record.args, tuple)
        assert record.args[2] == expected
