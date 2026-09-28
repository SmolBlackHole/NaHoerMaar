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
    causation_id: UUID | None
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
                causation_id=_uuid_field(record, "causation_id"),
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
                    entry.causation_id,
                    entry.actor_id,
                )
                self._next_id += 1
                self._entries.append(entry)
        except Exception:
            self.handleError(record)

    def entries(
        self,
        *,
        after: int | None = None,
        limit: int = 200,
        query: str | None = None,
        level: str | None = None,
        source: str | None = None,
        actor_id: UUID | None = None,
        request_id: str | None = None,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
    ) -> tuple[LogEntry, ...]:
        """Return matching entries newer than a cursor, oldest first."""
        if not 1 <= limit <= 200:
            raise ValueError("Log limit must be between 1 and 200.")
        cursor = after or 0
        needle = query.strip().casefold() if query else None
        expected_level = level.strip().upper() if level else None
        expected_source = source.strip() if source else None
        expected_request = request_id.strip() if request_id else None
        with self._entry_lock:
            matches = tuple(
                entry
                for entry in self._entries
                if entry.id > cursor
                and (expected_level is None or entry.level == expected_level)
                and (expected_source is None or entry.source == expected_source)
                and (actor_id is None or entry.actor_id == actor_id)
                and (expected_request is None or entry.request_id == expected_request)
                and (correlation_id is None or entry.correlation_id == correlation_id)
                and (causation_id is None or entry.causation_id == causation_id)
                and (needle is None or _search_text(entry, needle))
            )
        return matches[-limit:]


def _search_text(entry: LogEntry, needle: str) -> bool:
    values = (
        entry.level,
        entry.source,
        entry.message,
        entry.request_id,
        entry.message_id,
        entry.correlation_id,
        entry.causation_id,
        entry.actor_id,
    )
    return any(needle in str(value).casefold() for value in values if value is not None)


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
