# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import logging

import pytest

from nahormaar_backend.engine.observability import logged_operation


def test_logged_operation_times_success_without_logging_arguments(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @logged_operation("engine.test.request")
    async def request(url: str, *, opaque: str) -> str:
        return f"{url}:{opaque}"

    with caplog.at_level(logging.INFO):
        assert asyncio.run(request("private-url", opaque="private-token")) == (
            "private-url:private-token"
        )

    assert "engine.test.request.started" in caplog.text
    assert "engine.test.request.completed elapsed=" in caplog.text
    assert "private-url" not in caplog.text
    assert "private-token" not in caplog.text


def test_logged_operation_reports_error_type_without_error_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @logged_operation("engine.test.failure")
    async def request() -> None:
        raise ValueError("private upstream details")

    with caplog.at_level(logging.INFO), pytest.raises(ValueError):
        asyncio.run(request())

    assert "engine.test.failure.failed elapsed=" in caplog.text
    assert "error=ValueError" in caplog.text
    assert "private upstream details" not in caplog.text


def test_logged_operation_preserves_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @logged_operation("engine.test.cancel")
    async def request() -> None:
        raise asyncio.CancelledError

    with caplog.at_level(logging.INFO), pytest.raises(asyncio.CancelledError):
        asyncio.run(request())

    assert "engine.test.cancel.cancelled elapsed=" in caplog.text
    assert "engine.test.cancel.failed" not in caplog.text
