# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Explicit transaction boundary for application services."""

from collections.abc import Callable
import logging
from time import perf_counter
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession

SessionFactory = Callable[[], AsyncSession]
_LOGGER = logging.getLogger(__name__)


class UnitOfWork:
    """Own one explicit transaction and roll back incomplete work."""

    __slots__ = ("_committed", "_session", "_sessions", "_started_at")

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions
        self._session: AsyncSession | None = None
        self._committed = False
        self._started_at: float | None = None

    @property
    def session(self) -> AsyncSession:
        """Return the active session inside the context boundary."""
        if self._session is None:
            raise RuntimeError("UnitOfWork is not active.")
        return self._session

    async def __aenter__(self) -> "UnitOfWork":
        if self._session is not None:
            raise RuntimeError("UnitOfWork is already active.")
        session = self._sessions()
        try:
            await session.begin()
        except BaseException:
            await session.close()
            raise
        self._session = session
        self._committed = False
        self._started_at = perf_counter()
        _LOGGER.debug("database.transaction_started")
        return self

    async def commit(self) -> None:
        """Commit the active transaction exactly where the service decides."""
        await self.session.commit()
        self._committed = True
        _LOGGER.debug(
            "database.transaction_committed duration_ms=%.1f",
            self._duration_ms(),
        )

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session = self._session
        if session is None:
            return
        try:
            if not self._committed:
                await session.rollback()
                _LOGGER.debug(
                    "database.transaction_rolled_back duration_ms=%.1f exception=%s",
                    self._duration_ms(),
                    exception_type.__name__ if exception_type is not None else None,
                )
        finally:
            await session.close()
            self._session = None
            self._committed = False
            self._started_at = None

    def _duration_ms(self) -> float:
        started_at = self._started_at
        return 0.0 if started_at is None else (perf_counter() - started_at) * 1000
