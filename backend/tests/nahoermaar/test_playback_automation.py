# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from nahoermaar.listening.service import AudienceChanged, AudienceUnavailable
from nahoermaar.messaging import MessageBus, MessageContext
from nahoermaar.player.automation import PlaybackAutomation
from nahoermaar.player.domain import (
    ListeningSession,
    ListeningSessionId,
    PlayerState,
    SuspensionReason,
)
from nahoermaar.player.events import SuspendPlayback


class _Player:
    def __init__(self, state: PlayerState) -> None:
        self.state = state


def _state(*, sleep_at: datetime | None = None) -> PlayerState:
    now = datetime.now(UTC)
    state = PlayerState.empty(ListeningSessionId(uuid4()), now)
    return replace(
        state,
        session=ListeningSession(
            id=state.session.id,
            channel_id=42,
            created_at=now,
            updated_at=now,
            sleep_at=sleep_at,
        ),
    )


def _automation(
    state: PlayerState,
    *,
    grace: float = 0.02,
) -> tuple[PlaybackAutomation, list[SuspendPlayback]]:
    commands: list[SuspendPlayback] = []
    bus = MessageBus()

    async def suspend(
        command: SuspendPlayback,
        _context: MessageContext,
    ) -> None:
        commands.append(command)

    bus.register_command(SuspendPlayback, suspend)
    return (
        PlaybackAutomation(
            _Player(state),
            bus,
            empty_channel_grace_seconds=grace,
        ),
        commands,
    )


def test_human_return_cancels_one_empty_channel_grace() -> None:
    async def scenario() -> None:
        state = _state()
        automation, commands = _automation(state)
        context = MessageContext()
        empty = AudienceChanged(state.session.id, 0, frozenset(), frozenset())
        occupied = AudienceChanged(state.session.id, 1, frozenset(), frozenset())
        await automation.start()
        try:
            await automation.audience_changed(empty, context)
            await automation.audience_changed(empty, context)
            await asyncio.sleep(0.005)
            await automation.audience_changed(occupied, context)
            await asyncio.sleep(0.03)
            assert commands == []

            await automation.audience_changed(empty, context)
            await asyncio.sleep(0.03)
            assert [command.reason for command in commands] == [
                SuspensionReason.EMPTY_AUDIENCE
            ]
        finally:
            await automation.close()

    asyncio.run(scenario())


def test_audience_unavailable_cancels_pending_grace() -> None:
    async def scenario() -> None:
        state = _state()
        automation, commands = _automation(state)
        context = MessageContext()
        await automation.start()
        try:
            await automation.audience_changed(
                AudienceChanged(state.session.id, 0, frozenset(), frozenset()),
                context,
            )
            await automation.audience_unavailable(
                AudienceUnavailable(state.session.id),
                context,
            )
            await asyncio.sleep(0.03)
            assert commands == []
        finally:
            await automation.close()

    asyncio.run(scenario())


def test_sleep_timer_and_empty_channel_keep_independent_tasks() -> None:
    async def scenario() -> None:
        state = _state(sleep_at=datetime.now(UTC) + timedelta(seconds=0.02))
        automation, commands = _automation(state)
        await automation.start()
        try:
            await automation.audience_changed(
                AudienceChanged(state.session.id, 0, frozenset(), frozenset()),
                MessageContext(),
            )
            await asyncio.sleep(0.04)
            assert {command.reason for command in commands} == {
                SuspensionReason.EMPTY_AUDIENCE,
                SuspensionReason.SLEEP_TIMER,
            }
        finally:
            await automation.close()

    asyncio.run(scenario())


def test_restart_replaces_sleep_task_without_duplicate_command() -> None:
    async def scenario() -> None:
        state = _state(sleep_at=datetime.now(UTC) + timedelta(seconds=0.03))
        first, first_commands = _automation(state)
        await first.start()
        await asyncio.sleep(0.005)
        await first.close()

        second, second_commands = _automation(state)
        await second.start()
        try:
            await asyncio.sleep(0.04)
            assert first_commands == []
            assert [command.reason for command in second_commands] == [
                SuspensionReason.SLEEP_TIMER
            ]
        finally:
            await second.close()

    asyncio.run(scenario())
