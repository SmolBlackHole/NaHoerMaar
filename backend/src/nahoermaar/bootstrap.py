# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""The application's single composition root."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid5
from time import perf_counter
from zoneinfo import ZoneInfo

from .catalog.service import CatalogService

from .config import Settings
from .database.core import Database
from .database.schema import migrate
from .database.uow import UnitOfWork
from .integrations.discord import DiscordGateway
from .integrations.discord_oauth import DiscordOAuth
from .integrations.youtube import YouTubeMusicProvider, YouTubeProvider
from .listening.service import (
    AdvancePlayback,
    BeginPlayback,
    DisconnectAudience,
    FinishPlayback,
    ListeningService,
    ObserveAudience,
)
from .messaging import MessageBus, MessageContext
from .observability import configure_logging
from .operations.logs import RecentLogBuffer
from .player.domain import OperationId, PlayerError, PlayerErrorCode
from .player.events import (
    AddTracks,
    ApplyRadioCandidates,
    CheckpointPlayback,
    ClearQueue,
    CompletePlayback,
    FailPlayback,
    JoinVoice,
    LeaveVoice,
    MoveQueueEntry,
    MutationReply,
    Pause,
    Play,
    PlayerCommand,
    PlayerChanged,
    RadioRefillRequested,
    RemoveQueueEntry,
    Seek,
    SetCrossfade,
    SetVolume,
    Skip,
    RetryRadio,
    StartRadio,
    StopPlayback,
    StopRadio,
    UndoQueue,
)
from .player.playback import PlaybackCoordinator
from .player.session import CatalogRadioResolver, PlayerSessionManager
from .statistics.service import StatisticsService
from .users.domain import AccessEvent, User
from .users.service import (
    AccessService,
    AuthService,
    BeginLogin,
    CompleteLogin,
    GrantAccess,
    LoginCompletion,
    LoginStart,
    Logout,
    Operators,
    ReconcileOperators,
    RevokeAccess,
    SaveAppearance,
    SaveProfile,
    UserAccessChanged,
    UserLoggedIn,
    UserProfileChanged,
)
from .views.profile import ProfileView
from .views.recent import RecentListeningView

_LOGGER = logging.getLogger(__name__)
type AsyncCloser = Callable[[], Awaitable[None]]


class ApplicationLifecycle(StrEnum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    CLOSING = "closing"
    CLOSED = "closed"


def _closers() -> list[tuple[str, AsyncCloser]]:
    return []


@dataclass(slots=True)
class Application:
    """Dependencies owned by one application process."""

    settings: Settings
    database: Database
    bus: MessageBus
    auth: AuthService
    access: AccessService
    catalog: CatalogService
    player: PlayerSessionManager
    listening: ListeningService
    statistics: StatisticsService
    profiles: ProfileView
    recent: RecentListeningView
    logs: RecentLogBuffer
    gateway: DiscordGateway | None = None
    playback: PlaybackCoordinator | None = None
    _lifecycle: ApplicationLifecycle = field(
        default=ApplicationLifecycle.NEW,
        init=False,
        repr=False,
    )
    _lifecycle_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )
    _runtime_closers: list[tuple[str, AsyncCloser]] = field(
        default_factory=_closers,
        init=False,
        repr=False,
    )

    @property
    def lifecycle(self) -> ApplicationLifecycle:
        return self._lifecycle

    async def start(self) -> None:
        """Migrate storage and reconcile startup-owned state before requests."""
        async with self._lifecycle_lock:
            if self._lifecycle is ApplicationLifecycle.RUNNING:
                return
            if self._lifecycle is not ApplicationLifecycle.NEW:
                raise RuntimeError(
                    f"Application cannot start while {self._lifecycle.value}."
                )
            self._lifecycle = ApplicationLifecycle.STARTING
            started_at = perf_counter()
            _LOGGER.info("application.starting")
            try:
                await migrate(self.database.engine)
                await self.bus.execute(ReconcileOperators())
                self._runtime_closers.append(("player", self.player.close))
                await self.player.start()
                self._runtime_closers.append(("listening", self.listening.close))
                await self.listening.start(self.player.state.session.id)
                if self.gateway is not None:
                    self._runtime_closers.append(("discord", self.gateway.close))
                    await self.gateway.open()
                if self.playback is not None:
                    self._runtime_closers.append(("playback", self.playback.close))
                    await self.playback.start()
            except BaseException:
                _LOGGER.exception("application.start_failed")
                await self._close_owned(suppress=True)
                self._lifecycle = ApplicationLifecycle.CLOSED
                raise
            self._lifecycle = ApplicationLifecycle.RUNNING
            _LOGGER.info(
                "application.started session=%s duration_ms=%.1f",
                self.player.state.session.id,
                (perf_counter() - started_at) * 1000,
            )

    async def close(self) -> None:
        """Release process-owned resources."""
        async with self._lifecycle_lock:
            if self._lifecycle is ApplicationLifecycle.CLOSED:
                return
            self._lifecycle = ApplicationLifecycle.CLOSING
            started_at = perf_counter()
            _LOGGER.info("application.closing")
            failures = await self._close_owned(suppress=False)
            self._lifecycle = ApplicationLifecycle.CLOSED
            _LOGGER.info(
                "application.closed duration_ms=%.1f",
                (perf_counter() - started_at) * 1000,
            )
            if failures:
                raise ExceptionGroup("Application shutdown failed.", failures)

    async def _close_owned(self, *, suppress: bool) -> list[Exception]:
        closers = [
            *reversed(self._runtime_closers),
            ("catalog", self.catalog.close),
            ("database", self.database.close),
        ]
        self._runtime_closers.clear()
        failures: list[Exception] = []
        for resource, closer in closers:
            try:
                await closer()
            except Exception as error:
                failures.append(error)
                _LOGGER.exception(
                    "application.resource_close_failed resource=%s", resource
                )
        if failures and suppress:
            _LOGGER.error(
                "application.start_cleanup_failed resources=%d", len(failures)
            )
        return failures


def bootstrap(
    environ: Mapping[str, str] | None = None,
    *,
    dotenv_path: Path = Path(".env"),
) -> Application:
    """Load configuration and compose the application process."""
    settings = Settings.load(environ, dotenv_path=dotenv_path)
    logs = configure_logging(
        settings.log_level,
        settings.log_directory,
        settings.log_retention_days,
    )
    database = Database(settings.database_url)

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    access = AccessService(units, Operators.load(settings.auth.access_path))
    auth = AuthService(units, DiscordOAuth(settings.auth))
    catalog = CatalogService(
        units,
        (
            YouTubeProvider(settings.node_path),
            YouTubeMusicProvider(settings.node_path),
        ),
    )
    bus = MessageBus()
    player = PlayerSessionManager(units, bus, CatalogRadioResolver(catalog))
    listening = ListeningService(units, bus, access)
    statistics = StatisticsService(
        units,
        ZoneInfo(settings.statistics_timezone),
        access,
    )
    profiles = ProfileView(units, statistics)
    recent = RecentListeningView(units)

    async def summon(discord_id: str, channel_id: int, correlation_id: UUID) -> None:
        user = await access.require_discord_access(discord_id)
        context = MessageContext(correlation_id=correlation_id, actor_id=user.id)
        session_id = player.state.session.id
        await bus.execute(
            JoinVoice(
                session_id,
                OperationId(uuid5(correlation_id, "join-voice")),
                channel_id,
            ),
            context,
        )
        state = player.state
        if state.checkpoint.request is not None or state.queue.entries:
            try:
                await bus.execute(
                    Play(
                        session_id,
                        OperationId(uuid5(correlation_id, "play")),
                    ),
                    context,
                )
            except PlayerError as error:
                if error.code is not PlayerErrorCode.NOTHING_TO_PLAY:
                    raise

    gateway: DiscordGateway | None = None
    playback: PlaybackCoordinator | None = None
    if settings.discord.enabled:
        gateway = DiscordGateway(
            settings.discord.token,
            settings.discord.ffmpeg_path,
            settings.discord.quotes_path,
            summon,
        )
        playback = PlaybackCoordinator(
            player,
            catalog,
            listening,
            bus,
            gateway.output,
        )
    _register_handlers(bus, auth, access, player, listening, playback)
    _LOGGER.info("application.configured")
    return Application(
        settings,
        database,
        bus,
        auth,
        access,
        catalog,
        player,
        listening,
        statistics,
        profiles,
        recent,
        logs,
        gateway,
        playback,
    )


def _register_handlers(
    bus: MessageBus,
    auth: AuthService,
    access: AccessService,
    player: PlayerSessionManager,
    listening: ListeningService,
    playback: PlaybackCoordinator | None = None,
) -> None:
    async def begin_login(command: BeginLogin, _context: MessageContext) -> LoginStart:
        return await auth.begin(command.browser_token)

    async def complete_login(
        command: CompleteLogin, context: MessageContext
    ) -> LoginCompletion:
        result = await auth.complete(
            state=command.state,
            browser_token=command.browser_token,
            code=command.code,
            error=command.error,
            previous_session=command.previous_session,
        )
        await bus.publish(UserLoggedIn(result.user.id), context.child())
        return result

    async def logout(command: Logout, _context: MessageContext) -> None:
        await auth.logout(command.session_token)

    async def reconcile(
        _command: ReconcileOperators, context: MessageContext
    ) -> tuple[AccessEvent, ...]:
        changes = await access.reconcile()
        for change in changes:
            await bus.publish(UserAccessChanged(change), context.child())
        return changes

    async def grant(
        command: GrantAccess, context: MessageContext
    ) -> AccessEvent | None:
        change = await access.grant(command.actor_id, command.discord_id)
        if change is not None:
            await bus.publish(UserAccessChanged(change), context.child())
        return change

    async def revoke(
        command: RevokeAccess, context: MessageContext
    ) -> AccessEvent | None:
        change = await access.revoke(command.actor_id, command.discord_id)
        if change is not None:
            await bus.publish(UserAccessChanged(change), context.child())
        return change

    async def save_profile(command: SaveProfile, context: MessageContext) -> User:
        user = await auth.save_profile(command.user_id, command.profile)
        await bus.publish(UserProfileChanged(user.id), context.child())
        return user

    async def save_appearance(command: SaveAppearance, context: MessageContext) -> User:
        user = await auth.save_appearance(command.user_id, command.appearance)
        await bus.publish(UserProfileChanged(user.id), context.child())
        return user

    bus.register_command(BeginLogin, begin_login)
    bus.register_command(CompleteLogin, complete_login)
    bus.register_command(Logout, logout)
    bus.register_command(ReconcileOperators, reconcile)
    bus.register_command(GrantAccess, grant)
    bus.register_command(RevokeAccess, revoke)
    bus.register_command(SaveProfile, save_profile)
    bus.register_command(SaveAppearance, save_appearance)

    async def add_tracks(command: AddTracks, context: MessageContext) -> MutationReply:
        return await player.execute(command, context)

    async def remove_entry(
        command: RemoveQueueEntry, context: MessageContext
    ) -> MutationReply:
        return await player.execute(command, context)

    async def move_entry(
        command: MoveQueueEntry, context: MessageContext
    ) -> MutationReply:
        return await player.execute(command, context)

    async def clear_queue(
        command: ClearQueue, context: MessageContext
    ) -> MutationReply:
        return await player.execute(command, context)

    async def undo_queue(command: UndoQueue, context: MessageContext) -> MutationReply:
        return await player.execute(command, context)

    async def start_radio(
        command: StartRadio, context: MessageContext
    ) -> MutationReply:
        return await player.execute(command, context)

    async def stop_radio(command: StopRadio, context: MessageContext) -> MutationReply:
        return await player.execute(command, context)

    async def retry_radio(
        command: RetryRadio, context: MessageContext
    ) -> MutationReply:
        return await player.execute(command, context)

    async def apply_radio(
        command: ApplyRadioCandidates, context: MessageContext
    ) -> MutationReply:
        return await player.execute(command, context)

    async def playback_command(
        command: PlayerCommand,
        context: MessageContext,
    ) -> MutationReply:
        return await player.execute(command, context)

    async def reauthenticate_stream(
        _event: UserAccessChanged, _context: MessageContext
    ) -> None:
        player.events.reauthenticate()

    bus.register_command(AddTracks, add_tracks)
    bus.register_command(RemoveQueueEntry, remove_entry)
    bus.register_command(MoveQueueEntry, move_entry)
    bus.register_command(ClearQueue, clear_queue)
    bus.register_command(UndoQueue, undo_queue)
    bus.register_command(StartRadio, start_radio)
    bus.register_command(StopRadio, stop_radio)
    bus.register_command(RetryRadio, retry_radio)

    bus.register_command(ApplyRadioCandidates, apply_radio)
    bus.register_command(Play, playback_command)
    bus.register_command(Pause, playback_command)
    bus.register_command(Skip, playback_command)
    bus.register_command(StopPlayback, playback_command)
    bus.register_command(Seek, playback_command)
    bus.register_command(SetVolume, playback_command)
    bus.register_command(SetCrossfade, playback_command)
    bus.register_command(JoinVoice, playback_command)
    bus.register_command(LeaveVoice, playback_command)
    bus.register_command(CompletePlayback, playback_command)
    bus.register_command(FailPlayback, playback_command)
    bus.register_command(CheckpointPlayback, playback_command)
    bus.register_command(BeginPlayback, listening.begin)
    bus.register_command(AdvancePlayback, listening.advance)
    bus.register_command(FinishPlayback, listening.finish)
    bus.register_command(ObserveAudience, listening.observe)
    bus.register_command(DisconnectAudience, listening.disconnect)
    bus.subscribe(PlayerChanged, player.broadcast)
    if playback is not None:
        bus.subscribe(PlayerChanged, playback.player_changed)
    bus.subscribe(RadioRefillRequested, player.refill)
    bus.subscribe(UserAccessChanged, reauthenticate_stream)
