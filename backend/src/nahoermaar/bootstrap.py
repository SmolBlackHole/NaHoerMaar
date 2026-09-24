# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""The application's single composition root."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .database.core import Database
from .database.schema import migrate
from .database.uow import UnitOfWork
from .integrations.discord_oauth import DiscordOAuth
from .messaging import MessageBus, MessageContext
from .observability import configure_logging
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

    async def start(self) -> None:
        """Migrate storage and reconcile startup-owned state before requests."""
        await migrate(self.database.engine)
        await self.bus.execute(ReconcileOperators())
        _LOGGER.info("application.started")

    async def close(self) -> None:
        """Release process-owned resources."""
        await self.database.close()
        _LOGGER.info("application.closed")


def bootstrap(
    environ: Mapping[str, str] | None = None,
    *,
    dotenv_path: Path = Path(".env"),
) -> Application:
    """Load configuration and compose the application process."""
    settings = Settings.load(environ, dotenv_path=dotenv_path)
    configure_logging(settings.log_level)
    database = Database(settings.database_url)

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    access = AccessService(units, Operators.load(settings.auth.access_path))
    auth = AuthService(units, DiscordOAuth(settings.auth))
    bus = MessageBus()
    _register_handlers(bus, auth, access)
    _LOGGER.info("application.configured")
    return Application(settings, database, bus, auth, access)


def _event_context(context: MessageContext) -> MessageContext:
    return MessageContext(
        correlation_id=context.correlation_id,
        actor_id=context.actor_id,
    )


def _register_handlers(
    bus: MessageBus,
    auth: AuthService,
    access: AccessService,
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
