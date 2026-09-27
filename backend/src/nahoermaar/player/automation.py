# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Schedule unattended player commands without owning playback state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from typing import Protocol

from nahoermaar.listening.service import AudienceChanged, AudienceUnavailable
from nahoermaar.messaging import MessageBus, MessageContext

from .domain import ListeningSessionId, OperationId, PlayerState, SuspensionReason
from .events import PlayerChanged, SuspendPlayback

_LOGGER = logging.getLogger(__name__)


class PlayerStateSource(Protocol):
    @property
    def state(self) -> PlayerState: ...


class PlaybackAutomation:
    """Translate audience and sleep deadlines into serialized player commands."""

    __slots__ = (
        "_bus",
        "_closed",
        "_empty_deadline",
        "_empty_task",
        "_grace",
        "_player",
        "_sleep_deadline",
        "_sleep_task",
    )

    def __init__(
        self,
        player: PlayerStateSource,
        bus: MessageBus,
        *,
        empty_channel_grace_seconds: float,
    ) -> None:
        if empty_channel_grace_seconds <= 0:
            raise ValueError("Empty-channel grace must be positive.")
        self._player = player
        self._bus = bus
        self._grace = timedelta(seconds=empty_channel_grace_seconds)
        self._empty_task: asyncio.Task[None] | None = None
        self._empty_deadline: datetime | None = None
        self._sleep_task: asyncio.Task[None] | None = None
        self._sleep_deadline: datetime | None = None
        self._closed = True

    async def start(self) -> None:
        if not self._closed:
            return
        self._closed = False
        await self._replace_sleep_timer(self._player.state.session.sleep_at)
        _LOGGER.info(
            "playback.automation_started empty_channel_grace_seconds=%.1f",
            self._grace.total_seconds(),
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = tuple(
            task for task in (self._empty_task, self._sleep_task) if task is not None
        )
        self._empty_task = None
        self._empty_deadline = None
        self._sleep_task = None
        self._sleep_deadline = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        _LOGGER.info("playback.automation_closed")

    async def audience_changed(
        self,
        event: AudienceChanged,
        context: MessageContext,
    ) -> None:
        if self._closed or event.session_id != self._session_id:
            return
        if event.human_count > 0 or self._player.state.session.channel_id is None:
            await self._cancel_empty_timer("audience_returned")
            return
        task = self._empty_task
        if task is not None and not task.done():
            return
        deadline = datetime.now(UTC) + self._grace
        self._empty_deadline = deadline
        self._empty_task = asyncio.create_task(
            self._wait_for_empty_channel(deadline, context),
            name="playback-empty-channel-grace",
        )
        _LOGGER.info(
            "playback.empty_channel_grace_started session=%s deadline=%s",
            event.session_id,
            deadline.isoformat(),
        )

    async def audience_unavailable(
        self,
        event: AudienceUnavailable,
        _context: MessageContext,
    ) -> None:
        if event.session_id == self._session_id:
            await self._cancel_empty_timer("audience_unavailable")

    async def player_changed(
        self,
        event: PlayerChanged,
        _context: MessageContext,
    ) -> None:
        if self._closed or event.session_id != self._session_id:
            return
        await self._replace_sleep_timer(self._player.state.session.sleep_at)

    @property
    def _session_id(self) -> ListeningSessionId:
        return self._player.state.session.id

    async def _wait_for_empty_channel(
        self,
        deadline: datetime,
        parent: MessageContext,
    ) -> None:
        try:
            await asyncio.sleep(
                max(0.0, (deadline - datetime.now(UTC)).total_seconds())
            )
            if self._closed or self._empty_deadline != deadline:
                return
            self._empty_task = None
            self._empty_deadline = None
            context = parent.child()
            await self._bus.execute(
                SuspendPlayback(
                    self._session_id,
                    OperationId(context.message_id),
                    SuspensionReason.EMPTY_AUDIENCE,
                ),
                context,
            )
            _LOGGER.info(
                "playback.empty_channel_suspended session=%s",
                self._session_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "playback.empty_channel_suspend_failed session=%s",
                self._session_id,
            )

    async def _replace_sleep_timer(self, deadline: datetime | None) -> None:
        if deadline == self._sleep_deadline:
            task = self._sleep_task
            if deadline is None or (task is not None and not task.done()):
                return
        task = self._sleep_task
        self._sleep_task = None
        self._sleep_deadline = deadline
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if deadline is None or self._closed:
            return
        self._sleep_task = asyncio.create_task(
            self._wait_for_sleep_timer(deadline),
            name="playback-sleep-timer",
        )
        _LOGGER.info(
            "playback.sleep_timer_scheduled session=%s deadline=%s",
            self._session_id,
            deadline.isoformat(),
        )

    async def _wait_for_sleep_timer(self, deadline: datetime) -> None:
        try:
            await asyncio.sleep(
                max(0.0, (deadline - datetime.now(UTC)).total_seconds())
            )
            if self._closed or self._sleep_deadline != deadline:
                return
            self._sleep_task = None
            self._sleep_deadline = None
            context = MessageContext()
            await self._bus.execute(
                SuspendPlayback(
                    self._session_id,
                    OperationId(context.message_id),
                    SuspensionReason.SLEEP_TIMER,
                ),
                context,
            )
            _LOGGER.info("playback.sleep_timer_elapsed session=%s", self._session_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "playback.sleep_timer_failed session=%s",
                self._session_id,
            )

    async def _cancel_empty_timer(self, reason: str) -> None:
        task = self._empty_task
        if task is None:
            return
        self._empty_task = None
        self._empty_deadline = None
        if task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        _LOGGER.info(
            "playback.empty_channel_grace_cancelled session=%s reason=%s",
            self._session_id,
            reason,
        )
