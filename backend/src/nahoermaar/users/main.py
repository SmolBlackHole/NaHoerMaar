# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Users module composition."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from nahoermaar.database.uow import UnitOfWork
from nahoermaar.lifecycle import LifecycleResource
from nahoermaar.messaging import MessageBus, MessageContext
from nahoermaar.operations.maintenance import HousekeepingContribution

from .domain import AccessEvent, User
from .maintenance import UsersMaintenance
from .service import (
    AccessService,
    AuthService,
    BeginLogin,
    CompleteLogin,
    GrantAccess,
    IdentityProvider,
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

type Clock = Callable[[], datetime]
type UnitOfWorkFactory = Callable[[], UnitOfWork]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class UsersModule:
    """Application services and maintenance exported by Users."""

    auth: AuthService
    access: AccessService
    housekeeping: HousekeepingContribution
    lifecycle: LifecycleResource


def create_users_module(
    units: UnitOfWorkFactory,
    bus: MessageBus,
    provider: IdentityProvider,
    access_path: Path,
    *,
    clock: Clock = _utc_now,
) -> UsersModule:
    """Build Users, bind its messages and expose its startup reconciliation."""
    auth = AuthService(units, provider, clock=clock)
    access = AccessService(units, Operators.load(access_path), clock=clock)

    async def begin_login(
        command: BeginLogin,
        _context: MessageContext,
    ) -> LoginStart:
        return await auth.begin(command.browser_token, command.redirect_uri)

    async def complete_login(
        command: CompleteLogin,
        context: MessageContext,
    ) -> LoginCompletion:
        result = await auth.complete(
            state=command.state,
            browser_token=command.browser_token,
            code=command.code,
            error=command.error,
            previous_session=command.previous_session,
            redirect_uri=command.redirect_uri,
        )
        await bus.publish(UserLoggedIn(result.user.id), context.child())
        return result

    async def logout(command: Logout, _context: MessageContext) -> None:
        await auth.logout(command.session_token)

    async def reconcile(
        _command: ReconcileOperators,
        context: MessageContext,
    ) -> tuple[AccessEvent, ...]:
        changes = await access.reconcile()
        for change in changes:
            await bus.publish(UserAccessChanged(change), context.child())
        return changes

    async def grant(
        command: GrantAccess,
        context: MessageContext,
    ) -> AccessEvent | None:
        change = await access.grant(command.actor_id, command.discord_id)
        if change is not None:
            await bus.publish(UserAccessChanged(change), context.child())
        return change

    async def revoke(
        command: RevokeAccess,
        context: MessageContext,
    ) -> AccessEvent | None:
        change = await access.revoke(command.actor_id, command.discord_id)
        if change is not None:
            await bus.publish(UserAccessChanged(change), context.child())
        return change

    async def save_profile(command: SaveProfile, context: MessageContext) -> User:
        user = await auth.save_profile(command.user_id, command.profile)
        await bus.publish(UserProfileChanged(user.id), context.child())
        return user

    async def save_appearance(
        command: SaveAppearance,
        context: MessageContext,
    ) -> User:
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

    async def reconcile_operators() -> None:
        await bus.execute(ReconcileOperators())

    maintenance = UsersMaintenance(units)
    return UsersModule(
        auth,
        access,
        maintenance.contribution(),
        LifecycleResource("users.operators", start=reconcile_operators),
    )
