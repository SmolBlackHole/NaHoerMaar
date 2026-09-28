# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Integration asset composition."""

from dataclasses import dataclass
from pathlib import Path

from nahoermaar.operations.maintenance import HousekeepingContribution

from .avatars import DiscordAvatarStore
from .maintenance import IntegrationsMaintenance


@dataclass(frozen=True, slots=True)
class IntegrationsModule:
    """Shared integration assets and their bounded maintenance."""

    avatars: DiscordAvatarStore
    housekeeping: HousekeepingContribution


def create_integrations_module(avatar_directory: Path) -> IntegrationsModule:
    """Build integration-owned asset storage from process configuration."""
    avatars = DiscordAvatarStore(avatar_directory)
    return IntegrationsModule(
        avatars,
        IntegrationsMaintenance(avatars).contribution(),
    )
