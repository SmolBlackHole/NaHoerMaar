# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded in-memory access to recent process logs."""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One sanitized process log entry exposed to operators."""

    id: int
    timestamp: datetime
    level: str
    source: str
    message: str
    request_id: str | None
    message_id: UUID | None
    correlation_id: UUID | None
    actor_id: UUID | None


class RecentLogBuffer(logging.Handler):
    """Keep a bounded, thread-safe view of recent formatted records."""

    def __init__(self, capacity: int = 500) -> None:
        if capacity < 1:
            raise ValueError("Log capacity must be positive.")
        super().__init__()
        self._entries: deque[LogEntry] = deque(maxlen=capacity)
        self._entry_lock = threading.Lock()
        self._next_id = 1

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = " ".join(record.getMessage().splitlines())[:2000]
            entry = LogEntry(
                id=0,
                timestamp=datetime.fromtimestamp(record.created, UTC),
                level=record.levelname,
                source=_source(record.name),
                message=message,
                request_id=_text_field(record, "request_id"),
                message_id=_uuid_field(record, "message_id"),
                correlation_id=_uuid_field(record, "correlation_id"),
                actor_id=_uuid_field(record, "actor_id"),
            )
            with self._entry_lock:
                entry = LogEntry(
                    self._next_id,
                    entry.timestamp,
                    entry.level,
                    entry.source,
                    entry.message,
                    entry.request_id,
                    entry.message_id,
                    entry.correlation_id,
                    entry.actor_id,
                )
                self._next_id += 1
                self._entries.append(entry)
        except Exception:
            self.handleError(record)

    def entries(
        self, *, after: int | None = None, limit: int = 200
    ) -> tuple[LogEntry, ...]:
        """Return entries newer than an optional cursor, oldest first."""
        if not 1 <= limit <= 200:
            raise ValueError("Log limit must be between 1 and 200.")
        cursor = after or 0
        with self._entry_lock:
            matches = tuple(entry for entry in self._entries if entry.id > cursor)
        return matches[-limit:]


def _source(name: str) -> str:
    prefix = "nahoermaar."
    return name[len(prefix) :] if name.startswith(prefix) else name


def _text_field(record: logging.LogRecord, name: str) -> str | None:
    value = getattr(record, name, None)
    return str(value) if value not in {None, "-"} else None


def _uuid_field(record: logging.LogRecord, name: str) -> UUID | None:
    value = getattr(record, name, None)
    if isinstance(value, UUID):
        return value
    if value in {None, "-"}:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None
