# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Central logging configuration and request context for the engine."""

from __future__ import annotations

import copy
import logging
import os
from collections import deque
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from uvicorn.config import LOGGING_CONFIG

if TYPE_CHECKING:
    from .api_models import LogEntryView

_ACTOR_ID: ContextVar[str | None] = ContextVar("log_actor_id", default=None)
_ACTOR_NAME: ContextVar[str | None] = ContextVar("log_actor_name", default=None)
_TRACE_ID: ContextVar[str | None] = ContextVar("log_trace_id", default=None)
LOG_FILE = "backend.log"
LOG_RETENTION_DAYS = 14


def _single_line(value: object, limit: int = 120) -> str:
    return str(value).replace("\r", " ").replace("\n", " ")[:limit]


def actor_fields(
    actor_id: object | None,
    actor_name: object | None,
    trace_id: str | None = None,
) -> dict[str, object]:
    """Attach causality to a record emitted outside the originating task."""
    return {
        "actor_id": str(actor_id) if actor_id is not None else "-",
        "actor_name": _single_line(actor_name) if actor_name else "-",
        "trace_id": trace_id or _TRACE_ID.get() or "-",
    }


def current_trace_id() -> str | None:
    return _TRACE_ID.get()


def current_actor() -> tuple[str | None, str | None]:
    return _ACTOR_ID.get(), _ACTOR_NAME.get()


def request_trace_id(value: str | None = None) -> str:
    """Return a canonical UUID, replacing missing or untrusted request values."""
    if value is not None:
        try:
            return str(UUID(value))
        except (ValueError, AttributeError):
            pass
    return str(uuid4())


@contextmanager
def log_context(
    actor_id: object | None = None,
    actor_name: object | None = None,
    *,
    trace_id: str | None = None,
) -> Generator[None, None, None]:
    """Add request and actor context to every log record in the current task."""
    identifier = _ACTOR_ID.set(str(actor_id) if actor_id is not None else None)
    name = _ACTOR_NAME.set(_single_line(actor_name) if actor_name else None)
    trace = _TRACE_ID.set(trace_id or _TRACE_ID.get())
    try:
        yield
    finally:
        _TRACE_ID.reset(trace)
        _ACTOR_NAME.reset(name)
        _ACTOR_ID.reset(identifier)


class LogContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "actor_id"):
            record.actor_id = _ACTOR_ID.get() or "-"
        if not hasattr(record, "actor_name"):
            record.actor_name = _ACTOR_NAME.get() or "-"
        if not hasattr(record, "trace_id"):
            record.trace_id = _TRACE_ID.get() or "-"
        return True


class AccessLogFilter(logging.Filter):
    """Keep credentials, searches and media references out of access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, path, protocol, status = record.args
            if isinstance(path, str):
                record.args = (client, method, path.split("?", 1)[0], protocol, status)
        return True


def logging_configuration(directory: Path | None = None) -> dict[str, Any]:
    """Build one console and daily rotating-file configuration for Uvicorn."""
    log_directory = directory or Path(os.environ.get("NAHORMAAR_LOG_DIR", "data/logs"))
    log_directory.mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = copy.deepcopy(LOGGING_CONFIG)
    prefix = (
        "%(asctime)s pid=%(process)d task=%(taskName)r "
        "trace_id=%(trace_id)s actor_id=%(actor_id)s actor_name=%(actor_name)r "
    )
    for formatter in config["formatters"].values():
        formatter["fmt"] = prefix + formatter["fmt"]
        formatter["datefmt"] = "%Y-%m-%dT%H:%M:%S%z"
    config["filters"] = {
        "access_secrets": {"()": AccessLogFilter},
        "context": {"()": LogContextFilter},
    }
    for handler in config["handlers"].values():
        handler["filters"] = ["context", "access_secrets"]
        handler["level"] = "INFO"
    config["handlers"]["file"] = {
        "class": "logging.handlers.TimedRotatingFileHandler",
        "formatter": "default",
        "filters": ["context", "access_secrets"],
        "filename": str(log_directory / LOG_FILE),
        "when": "midnight",
        "interval": 1,
        "backupCount": LOG_RETENTION_DAYS,
        "encoding": "utf-8",
        "utc": True,
        "delay": True,
        "level": "DEBUG",
    }
    config["loggers"]["uvicorn"]["handlers"] = ["default", "file"]
    config["loggers"]["uvicorn.access"]["handlers"] = ["access", "file"]
    config["loggers"]["nahormaar_backend"] = {
        "handlers": ["default", "file"],
        "level": "DEBUG",
        "propagate": False,
    }
    return config


class RecentLogs(logging.Handler):
    def __init__(self, capacity: int = 500) -> None:
        super().__init__(level=logging.DEBUG)
        self.addFilter(LogContextFilter())
        self._entries: deque[LogEntryView] = deque(maxlen=capacity)
        self._sequence = 0
        self._entries_lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        from .api_models import LogEntryView

        message = record.getMessage().replace("\r", " ").replace("\n", " ")[:2000]
        actor_id = getattr(record, "actor_id", "-")
        actor_name = getattr(record, "actor_name", "-")
        trace_id = getattr(record, "trace_id", "-")
        with self._entries_lock:
            self._sequence += 1
            self._entries.append(
                LogEntryView(
                    id=self._sequence,
                    timestamp=datetime.fromtimestamp(record.created, UTC),
                    level=record.levelname,
                    source=record.name.removeprefix("nahormaar_backend."),
                    message=message,
                    actor_id=None if actor_id == "-" else str(actor_id),
                    actor_name=None if actor_name == "-" else str(actor_name),
                    trace_id=None if trace_id == "-" else str(trace_id),
                )
            )

    def since(self, after: int | None = None) -> tuple[LogEntryView, ...]:
        with self._entries_lock:
            if after is None:
                return tuple(self._entries)[-200:]
            return tuple(entry for entry in self._entries if entry.id > after)[:200]
