# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""A bounded, process-local tail of bot diagnostics for the admin console."""

import logging
from collections import deque
from datetime import UTC, datetime
from threading import Lock

from .api_models import LogEntryView


class RecentLogs(logging.Handler):
    def __init__(self, capacity: int = 500) -> None:
        super().__init__(level=logging.INFO)
        self._entries: deque[LogEntryView] = deque(maxlen=capacity)
        self._sequence = 0
        self._lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage().replace("\r", " ").replace("\n", " ")[:2000]
        with self._lock:
            self._sequence += 1
            self._entries.append(
                LogEntryView(
                    id=self._sequence,
                    timestamp=datetime.fromtimestamp(record.created, UTC),
                    level=record.levelname,
                    source=record.name.removeprefix("nahormaar_backend."),
                    message=message,
                )
            )

    def since(self, after: int | None = None) -> tuple[LogEntryView, ...]:
        with self._lock:
            if after is None:
                return tuple(self._entries)[-200:]
            return tuple(entry for entry in self._entries if entry.id > after)[:200]
