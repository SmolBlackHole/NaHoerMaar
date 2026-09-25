# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""The application's single composition root."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from .catalog.service import CatalogService

from .config import Settings
from .database.core import Database
from .database.schema import migrate
from .database.uow import UnitOfWork
from .integrations.discord_oauth import DiscordOAuth
from .integrations.youtube import YouTubeProvider
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
from .player.events import (
    AddTracks,
    ApplyRadioCandidates,
    ClearQueue,
    MoveQueueEntry,
    MutationReply,
    PlayerChanged,
    RadioRefillRequested,
    RemoveQueueEntry,
    RetryRadio,
    StartRadio,
    StopRadio,
    UndoQueue,
)
from .player.session import CatalogRadioResolver, PlayerSessionManager
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

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
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
    logs: RecentLogBuffer

    async def start(self) -> None:
        """Migrate storage and reconcile startup-owned state before requests."""
        started_at = perf_counter()
        _LOGGER.info("application.starting")
        await migrate(self.database.engine)
        await self.bus.execute(ReconcileOperators())
        await self.player.start()
        await self.listening.start(self.player.state.session.id)
        _LOGGER.info(
            "application.started session=%s duration_ms=%.1f",
            self.player.state.session.id,
            (perf_counter() - started_at) * 1000,
        )

    async def close(self) -> None:
        """Release process-owned resources."""
        started_at = perf_counter()
        _LOGGER.info("application.closing")
        await self.player.close()
        await self.catalog.close()
        await self.database.close()
        _LOGGER.info(
            "application.closed duration_ms=%.1f",
            (perf_counter() - started_at) * 1000,
        )


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
    catalog = CatalogService(units, (YouTubeProvider(settings.node_path),))
    bus = MessageBus()
    player = PlayerSessionManager(units, bus, CatalogRadioResolver(catalog))
    listening = ListeningService(units, bus)
    _register_handlers(bus, auth, access, player, listening)
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
        logs,
    )


def _event_context(context: MessageContext) -> MessageContext:
    return MessageContext(
        correlation_id=context.correlation_id,
        actor_id=context.actor_id,
    )


def _register_handlers(
    bus: MessageBus,
    auth: AuthService,
    access: AccessService,
    player: PlayerSessionManager,
    listening: ListeningService,
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
        await bus.publish(UserLoggedIn(result.user.id), _event_context(context))
        return result

    async def logout(command: Logout, _context: MessageContext) -> None:
        await auth.logout(command.session_token)

    async def reconcile(
        _command: ReconcileOperators, context: MessageContext
    ) -> tuple[AccessEvent, ...]:
        changes = await access.reconcile()
        for change in changes:
            await bus.publish(UserAccessChanged(change), _event_context(context))
        return changes

    async def grant(
        command: GrantAccess, context: MessageContext
    ) -> AccessEvent | None:
        change = await access.grant(command.actor_id, command.discord_id)
        if change is not None:
            await bus.publish(UserAccessChanged(change), _event_context(context))
        return change

    async def revoke(
        command: RevokeAccess, context: MessageContext
    ) -> AccessEvent | None:
        change = await access.revoke(command.actor_id, command.discord_id)
        if change is not None:
            await bus.publish(UserAccessChanged(change), _event_context(context))
        return change

    async def save_profile(command: SaveProfile, context: MessageContext) -> User:
        user = await auth.save_profile(command.user_id, command.profile)
        await bus.publish(UserProfileChanged(user.id), _event_context(context))
        return user

    async def save_appearance(command: SaveAppearance, context: MessageContext) -> User:
        user = await auth.save_appearance(command.user_id, command.appearance)
        await bus.publish(UserProfileChanged(user.id), _event_context(context))
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
    bus.register_command(BeginPlayback, listening.begin)
    bus.register_command(AdvancePlayback, listening.advance)
    bus.register_command(FinishPlayback, listening.finish)
    bus.register_command(ObserveAudience, listening.observe)
    bus.register_command(DisconnectAudience, listening.disconnect)
    bus.subscribe(PlayerChanged, player.broadcast)
    bus.subscribe(RadioRefillRequested, player.refill)
    bus.subscribe(UserAccessChanged, reauthenticate_stream)
