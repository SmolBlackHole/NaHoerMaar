# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Process-wide logging with request and message correlation."""

from __future__ import annotations

import logging
import re
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from uuid import UUID, uuid4

from .config import LogLevel
from .operations.logs import RecentLogBuffer

_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
_MESSAGE_ID: ContextVar[UUID | None] = ContextVar("message_id", default=None)
_CORRELATION_ID: ContextVar[UUID | None] = ContextVar("correlation_id", default=None)
_CAUSATION_ID: ContextVar[UUID | None] = ContextVar("causation_id", default=None)
_ACTOR_ID: ContextVar[UUID | None] = ContextVar("actor_id", default=None)
_OWNED_HANDLERS: set[logging.Handler] = set()
_URL_QUERY = re.compile(r'(https?://[^\s"?]+)\?[^\s"]+')
_SECRET_VALUE = re.compile(
    r"(?i)\b(access_token|refresh_token|token|code|state|cookie|authorization|client_secret)="
    r"([^\s&\"']+)"
)


@dataclass(frozen=True, slots=True)
class LogContext:
    """Correlation values bound to the current asynchronous operation."""

    request_id: str | None = None
    message_id: UUID | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    actor_id: UUID | None = None


class ContextFilter(logging.Filter):
    """Attach the current operation context to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize_log_message(record.getMessage())
        record.args = ()
        record.request_id = _context_value(record, "request_id", _REQUEST_ID.get())
        record.message_id = _context_value(record, "message_id", _MESSAGE_ID.get())
        record.correlation_id = _context_value(
            record, "correlation_id", _CORRELATION_ID.get()
        )
        record.causation_id = _context_value(
            record, "causation_id", _CAUSATION_ID.get()
        )
        record.actor_id = _context_value(record, "actor_id", _ACTOR_ID.get())
        return True


@contextmanager
def log_context(context: LogContext) -> Generator[None]:
    """Bind non-empty context fields for the duration of one operation."""
    request_token = (
        _REQUEST_ID.set(context.request_id) if context.request_id is not None else None
    )
    message_token = (
        _MESSAGE_ID.set(context.message_id) if context.message_id is not None else None
    )
    correlation_token = (
        _CORRELATION_ID.set(context.correlation_id)
        if context.correlation_id is not None
        else None
    )
    causation_token = (
        _CAUSATION_ID.set(context.causation_id)
        if context.causation_id is not None
        else None
    )
    actor_token = (
        _ACTOR_ID.set(context.actor_id) if context.actor_id is not None else None
    )
    try:
        yield
    finally:
        if actor_token is not None:
            _ACTOR_ID.reset(actor_token)
        if causation_token is not None:
            _CAUSATION_ID.reset(causation_token)
        if correlation_token is not None:
            _CORRELATION_ID.reset(correlation_token)
        if message_token is not None:
            _MESSAGE_ID.reset(message_token)
        if request_token is not None:
            _REQUEST_ID.reset(request_token)


def current_correlation_id() -> UUID:
    """Return the active correlation or start a new operation chain."""
    return _CORRELATION_ID.get() or uuid4()


def current_actor_id() -> UUID | None:
    """Return the authenticated actor bound to the current operation."""
    return _ACTOR_ID.get()


def safe_log_value(value: str, *, limit: int = 200) -> str:
    """Make user-controlled text safe for one bounded log line."""
    return " ".join(value.splitlines())[:limit]


def sanitize_log_message(message: str) -> str:
    """Remove query strings and common credentials from any logger message."""
    single_line = " ".join(message.splitlines())
    without_queries = _URL_QUERY.sub(r"\1", single_line)
    return _SECRET_VALUE.sub(r"\1=<redacted>", without_queries)


def _context_value(
    record: logging.LogRecord,
    name: str,
    current: object | None,
) -> object:
    existing = getattr(record, name, None)
    if existing is None or existing == "-":
        return current or "-"
    return existing


def _utc_time(timestamp: float | None = None) -> time.struct_time:
    return time.gmtime(timestamp)


def error_code(error: BaseException) -> str:
    """Return a stable domain code when an exception exposes one."""
    code = getattr(error, "code", None)
    value = getattr(code, "value", None)
    return str(value) if value is not None else type(error).__name__


def configure_logging(
    level: LogLevel,
    directory: Path,
    retention_days: int,
) -> RecentLogBuffer:
    """Configure correlated console, rotating file and recent-memory logs."""
    directory.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(name)s "
        "request_id=%(request_id)s message_id=%(message_id)s "
        "correlation_id=%(correlation_id)s causation_id=%(causation_id)s "
        "actor_id=%(actor_id)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = _utc_time
    context_filter = ContextFilter()

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.addFilter(context_filter)

    file_handler = TimedRotatingFileHandler(
        directory / "nahoermaar.log",
        when="midnight",
        backupCount=retention_days,
        encoding="utf-8",
        delay=True,
        utc=True,
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(context_filter)

    recent = RecentLogBuffer()
    recent.addFilter(context_filter)

    root = logging.getLogger()
    for handler in tuple(_OWNED_HANDLERS):
        if handler in root.handlers:
            root.removeHandler(handler)
        handler.close()
    _OWNED_HANDLERS.clear()
    _OWNED_HANDLERS.update((console, file_handler, recent))
    root.addHandler(console)
    root.addHandler(file_handler)
    root.addHandler(recent)
    root.setLevel(level.value)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            handler.close()
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return recent


def close_logging() -> None:
    """Release process-owned logging handlers for short-lived tooling."""
    root = logging.getLogger()
    for handler in tuple(_OWNED_HANDLERS):
        if handler in root.handlers:
            root.removeHandler(handler)
        handler.close()
    _OWNED_HANDLERS.clear()
