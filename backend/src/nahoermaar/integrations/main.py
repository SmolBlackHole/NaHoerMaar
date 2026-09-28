# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""External integration composition and immutable completion stages."""

from dataclasses import dataclass
from pathlib import Path

from nahoermaar.catalog.providers import CatalogProvider
from nahoermaar.config import AuthSettings, DiscordSettings
from nahoermaar.lifecycle import LifecycleResource
from nahoermaar.lyrics.providers import LyricsProvider
from nahoermaar.operations.maintenance import HousekeepingContribution
from nahoermaar.player.playback import PlaybackTransport
from nahoermaar.users.service import IdentityProvider

from .avatars import DiscordAvatarStore
from .discord import DiscordGateway, SummonHandler
from .discord_oauth import DiscordOAuth
from .maintenance import IntegrationsMaintenance
from .lrclib import LrclibProvider
from .youtube import YouTubeMusicProvider, YouTubeProvider


@dataclass(frozen=True, slots=True)
class IntegrationsPreparation:
    """Adapters that can be built before the Player summon use case exists."""

    identity: IdentityProvider
    catalog_providers: tuple[CatalogProvider, ...]
    lyrics_provider: LyricsProvider
    avatars: DiscordAvatarStore
    housekeeping: HousekeepingContribution
    discord: DiscordSettings


@dataclass(frozen=True, slots=True)
class IntegrationsModule:
    """Completed external adapters and their owned runtime resources."""

    identity: IdentityProvider
    catalog_providers: tuple[CatalogProvider, ...]
    lyrics_provider: LyricsProvider
    avatars: DiscordAvatarStore
    housekeeping: HousekeepingContribution
    gateway: DiscordGateway | None
    playback_transport: PlaybackTransport | None
    gateway_lifecycle: LifecycleResource | None


def prepare_integrations_module(
    auth: AuthSettings,
    discord: DiscordSettings,
    *,
    node_path: Path,
    avatar_directory: Path,
) -> IntegrationsPreparation:
    """Build adapters that do not depend on the prepared Player module."""
    avatars = DiscordAvatarStore(avatar_directory)
    return IntegrationsPreparation(
        identity=DiscordOAuth(auth),
        catalog_providers=(
            YouTubeProvider(node_path),
            YouTubeMusicProvider(node_path),
        ),
        lyrics_provider=LrclibProvider(),
        avatars=avatars,
        housekeeping=IntegrationsMaintenance(avatars).contribution(),
        discord=discord,
    )


def complete_integrations_module(
    preparation: IntegrationsPreparation,
    summon: SummonHandler,
) -> IntegrationsModule:
    """Complete optional Discord integrations with the Player summon use case."""
    gateway = (
        DiscordGateway(
            preparation.discord.token,
            preparation.discord.ffmpeg_path,
            preparation.discord.quotes_path,
            summon,
        )
        if preparation.discord.enabled
        else None
    )
    return IntegrationsModule(
        identity=preparation.identity,
        catalog_providers=preparation.catalog_providers,
        lyrics_provider=preparation.lyrics_provider,
        avatars=preparation.avatars,
        housekeeping=preparation.housekeeping,
        gateway=gateway,
        playback_transport=gateway.output if gateway is not None else None,
        gateway_lifecycle=(
            LifecycleResource(
                "discord",
                start=gateway.open,
                close=gateway.close,
            )
            if gateway is not None
            else None
        ),
    )
