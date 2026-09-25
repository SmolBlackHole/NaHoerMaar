# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Serialized use cases for durable playback and listener facts."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging

from nahoermaar.database.uow import UnitOfWork
from nahoermaar.messaging import Command, Event, MessageBus, MessageContext
from nahoermaar.player.domain import ListeningSessionId, TrackRequestId
from nahoermaar.users.domain import DiscordIdentity, UserId
from nahoermaar.users.repository import UserRepository

from .domain import (
    AudienceMember,
    AudienceState,
    ListeningError,
    ListeningErrorCode,
    PlaybackEndReason,
    PlaybackProgress,
    PlaybackRecord,
    PlaybackRecordId,
)
from .repository import ListeningRepository

type UnitFactory = Callable[[], UnitOfWork]

_LOGGER = logging.getLogger(__name__)


def _aware(value: datetime, label: str) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware.")


@dataclass(frozen=True, slots=True)
class VoiceMemberState:
    """One Discord voice member as observed by the adapter."""

    discord_id: str
    bot: bool
    deafened: bool

    def __post_init__(self) -> None:
        DiscordIdentity(self.discord_id)


@dataclass(frozen=True, slots=True)
class BeginPlayback(Command[PlaybackRecord]):
    """Confirm a play only after the first audio frame reached Discord."""

    playback_id: PlaybackRecordId
    session_id: ListeningSessionId
    request_id: TrackRequestId
    started_at: datetime

    def __post_init__(self) -> None:
        _aware(self.started_at, "Playback start")


@dataclass(frozen=True, slots=True)
class AdvancePlayback(Command[tuple[PlaybackRecord, ...]]):
    """Persist absolute emitted-audio counters, never media seek positions."""

    session_id: ListeningSessionId
    progress: tuple[PlaybackProgress, ...]
    credited_playback_id: PlaybackRecordId
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.progress:
            raise ValueError("Playback progress cannot be empty.")
        if type(self.progress) is not tuple:
            raise ValueError("Playback progress must be an immutable tuple.")
        if self.credited_playback_id not in {
            item.playback_id for item in self.progress
        }:
            raise ValueError("Credited playback must be part of the progress sample.")
        _aware(self.observed_at, "Playback observation")


@dataclass(frozen=True, slots=True)
class FinishPlayback(Command[PlaybackRecord]):
    """Close a confirmed play after its final audio progress was recorded."""

    playback_id: PlaybackRecordId
    ended_at: datetime
    reason: PlaybackEndReason

    def __post_init__(self) -> None:
        _aware(self.ended_at, "Playback end")


@dataclass(frozen=True, slots=True)
class ObserveAudience(Command[AudienceState]):
    """Reconcile one fresh Discord channel snapshot after flushing audio."""

    session_id: ListeningSessionId
    members: tuple[VoiceMemberState, ...]
    observed_at: datetime

    def __post_init__(self) -> None:
        if type(self.members) is not tuple:
            raise ValueError("Voice members must be an immutable tuple.")
        identifiers = tuple(member.discord_id for member in self.members)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Voice members must be unique.")
        _aware(self.observed_at, "Audience observation")


@dataclass(frozen=True, slots=True)
class DisconnectAudience(Command[AudienceState]):
    """Invalidate voice presence after flushing audio at disconnect time."""

    session_id: ListeningSessionId
    disconnected_at: datetime

    def __post_init__(self) -> None:
        _aware(self.disconnected_at, "Audience disconnect")


@dataclass(frozen=True, slots=True)
class PlaybackStarted(Event):
    playback_id: PlaybackRecordId
    session_id: ListeningSessionId
    request_id: TrackRequestId


@dataclass(frozen=True, slots=True)
class PlaybackAdvanced(Event):
    session_id: ListeningSessionId
    progress: tuple[PlaybackProgress, ...]
    credited_playback_id: PlaybackRecordId


@dataclass(frozen=True, slots=True)
class PlaybackEnded(Event):
    playback_id: PlaybackRecordId
    session_id: ListeningSessionId
    reason: PlaybackEndReason


@dataclass(frozen=True, slots=True)
class AudienceChanged(Event):
    session_id: ListeningSessionId
    human_count: int
    user_ids: frozenset[UserId]
    audible_user_ids: frozenset[UserId]


@dataclass(frozen=True, slots=True)
class AudienceUnavailable(Event):
    session_id: ListeningSessionId


class ListeningService:
    """Own the ordered write boundary for listening measurements."""

    __slots__ = ("_audience", "_bus", "_lock", "_units")

    def __init__(self, units: UnitFactory, bus: MessageBus) -> None:
        self._units = units
        self._bus = bus
        self._lock = asyncio.Lock()
        self._audience: AudienceState | None = None

    @property
    def audience(self) -> AudienceState:
        audience = self._audience
        if audience is None:
            raise RuntimeError("ListeningService is not started.")
        return audience

    async def start(self, session_id: ListeningSessionId) -> None:
        async with self._lock:
            async with self._units() as work:
                self._audience = await ListeningRepository(
                    work.session
                ).suspend_audience(session_id)
                await work.commit()
        _LOGGER.info(
            "listening.started session=%s restored_users=%d",
            session_id,
            len(self.audience.members),
        )

    async def begin(
        self,
        command: BeginPlayback,
        context: MessageContext,
    ) -> PlaybackRecord:
        record = PlaybackRecord(
            command.playback_id,
            command.session_id,
            command.request_id,
            command.started_at,
        )
        async with self._lock:
            async with self._units() as work:
                result, changed = await ListeningRepository(
                    work.session
                ).start_playback(record)
                await work.commit()
        if changed:
            _LOGGER.info(
                "listening.playback_started playback_id=%s session=%s request_id=%s",
                result.id,
                result.session_id,
                result.request_id,
            )
            await self._bus.publish(
                PlaybackStarted(result.id, result.session_id, result.request_id),
                _event_context(context),
            )
        return result

    async def advance(
        self,
        command: AdvancePlayback,
        context: MessageContext,
    ) -> tuple[PlaybackRecord, ...]:
        async with self._lock:
            audience = self.audience
            if audience.session_id != command.session_id:
                raise ListeningError(ListeningErrorCode.AUDIENCE_SESSION_MISMATCH)
            effective = (
                audience
                if audience.observed_at is not None
                else AudienceState(command.session_id, 0, 0, (), command.observed_at)
            )
            async with self._units() as work:
                result, changed = await ListeningRepository(
                    work.session
                ).advance_playback(
                    command.session_id,
                    command.progress,
                    credited_playback_id=command.credited_playback_id,
                    audience=effective,
                    observed_at=command.observed_at,
                )
                await work.commit()
        if changed:
            _LOGGER.debug(
                "listening.playback_advanced session=%s playbacks=%d credited_playback_id=%s humans=%d audible_humans=%d audible_users=%d",
                command.session_id,
                len(command.progress),
                command.credited_playback_id,
                effective.human_count,
                effective.audible_human_count,
                len(effective.audible_user_ids),
            )
            await self._bus.publish(
                PlaybackAdvanced(
                    command.session_id,
                    command.progress,
                    command.credited_playback_id,
                ),
                _event_context(context),
            )
        return result

    async def finish(
        self,
        command: FinishPlayback,
        context: MessageContext,
    ) -> PlaybackRecord:
        async with self._lock:
            async with self._units() as work:
                result, changed = await ListeningRepository(
                    work.session
                ).finish_playback(
                    command.playback_id,
                    ended_at=command.ended_at,
                    reason=command.reason,
                )
                await work.commit()
        if changed:
            _LOGGER.info(
                "listening.playback_finished playback_id=%s session=%s reason=%s",
                result.id,
                result.session_id,
                command.reason.value,
            )
            await self._bus.publish(
                PlaybackEnded(result.id, result.session_id, command.reason),
                _event_context(context),
            )
        return result

    async def observe(
        self,
        command: ObserveAudience,
        context: MessageContext,
    ) -> AudienceState:
        async with self._lock:
            previous = self.audience
            if previous.session_id != command.session_id:
                raise ListeningError(ListeningErrorCode.AUDIENCE_SESSION_MISMATCH)
            humans = tuple(member for member in command.members if not member.bot)
            async with self._units() as work:
                users = await UserRepository(work.session).active_ids_by_discord_ids(
                    member.discord_id for member in humans
                )
                audience = AudienceState(
                    command.session_id,
                    len(humans),
                    sum(not member.deafened for member in humans),
                    tuple(
                        AudienceMember(users[member.discord_id], member.deafened)
                        for member in humans
                        if member.discord_id in users
                    ),
                    command.observed_at,
                )
                await ListeningRepository(work.session).reconcile_audience(audience)
                await work.commit()
            self._audience = audience
            changed = (
                previous.human_count != audience.human_count
                or previous.audible_human_count != audience.audible_human_count
                or previous.members != audience.members
                or previous.observed_at is None
            )
        if changed:
            _LOGGER.info(
                "listening.audience_changed session=%s humans=%d audible_humans=%d authorized_users=%d audible_users=%d",
                audience.session_id,
                audience.human_count,
                audience.audible_human_count,
                len(audience.user_ids),
                len(audience.audible_user_ids),
            )
            await self._bus.publish(
                AudienceChanged(
                    audience.session_id,
                    audience.human_count,
                    audience.user_ids,
                    audience.audible_user_ids,
                ),
                _event_context(context),
            )
        return audience

    async def disconnect(
        self,
        command: DisconnectAudience,
        context: MessageContext,
    ) -> AudienceState:
        async with self._lock:
            previous = self.audience
            if previous.session_id != command.session_id:
                raise ListeningError(ListeningErrorCode.AUDIENCE_SESSION_MISMATCH)
            async with self._units() as work:
                await ListeningRepository(work.session).suspend_audience(
                    command.session_id,
                    disconnected_at=command.disconnected_at,
                )
                await work.commit()
            unavailable = AudienceState(
                command.session_id,
                previous.human_count,
                previous.audible_human_count,
                previous.members,
                None,
            )
            self._audience = unavailable
        if previous.observed_at is not None:
            _LOGGER.info(
                "listening.audience_disconnected session=%s humans=%d authorized_users=%d",
                command.session_id,
                previous.human_count,
                len(previous.user_ids),
            )
            await self._bus.publish(
                AudienceUnavailable(command.session_id),
                _event_context(context),
            )
        return unavailable


def _event_context(context: MessageContext) -> MessageContext:
    return MessageContext(
        correlation_id=context.correlation_id,
        actor_id=context.actor_id,
    )
