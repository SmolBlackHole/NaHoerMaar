# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Explicit transaction boundary for application services."""

from collections.abc import Callable
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession

SessionFactory = Callable[[], AsyncSession]


class UnitOfWork:
    """Own one explicit transaction and roll back incomplete work."""

    __slots__ = ("_committed", "_session", "_sessions")

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions
        self._session: AsyncSession | None = None
        self._committed = False

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
        return self

    async def commit(self) -> None:
        """Commit the active transaction exactly where the service decides."""
        await self.session.commit()
        self._committed = True

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
        finally:
            await session.close()
            self._session = None
            self._committed = False
