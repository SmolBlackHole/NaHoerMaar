# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Users module composition."""

from collections.abc import Callable
from dataclasses import dataclass

from nahoermaar.database.uow import UnitOfWork
from nahoermaar.operations.maintenance import HousekeepingContribution

from .maintenance import UsersMaintenance
from .service import AccessService, AuthService, IdentityProvider, Operators

type UnitOfWorkFactory = Callable[[], UnitOfWork]


@dataclass(frozen=True, slots=True)
class UsersModule:
    """Application services and maintenance exported by Users."""

    auth: AuthService
    access: AccessService
    housekeeping: HousekeepingContribution


def create_users_module(
    units: UnitOfWorkFactory,
    provider: IdentityProvider,
    operators: Operators,
) -> UsersModule:
    """Build Users services and their bounded maintenance contribution."""
    maintenance = UsersMaintenance(units)
    return UsersModule(
        AuthService(units, provider),
        AccessService(units, operators),
        maintenance.contribution(),
    )
