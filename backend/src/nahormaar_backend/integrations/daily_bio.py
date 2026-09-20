# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Keep the application's description on one quote per local calendar day."""

import asyncio
import logging
import random
import tomllib
from datetime import date
from pathlib import Path
from typing import cast

import discord

_CHECK_INTERVAL_SECONDS = 60.0
_DESCRIPTION_LIMIT = 400
_LOGGER = logging.getLogger(__name__)


def load_quotes(path: Path) -> tuple[str, ...]:
    with path.open("rb") as source:
        languages: object = tomllib.load(source).get("quotes")
    if not isinstance(languages, dict) or not languages:
        raise ValueError("quotes must be a non-empty table of language arrays.")
    quotes: list[str] = []
    for language, entries in cast(dict[str, object], languages).items():
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"quotes.{language} must be a non-empty array.")
        for entry in cast(list[object], entries):
            if (
                not isinstance(entry, str)
                or not entry.strip()
                or len(entry.strip()) > _DESCRIPTION_LIMIT
            ):
                raise ValueError(
                    f"quotes.{language} must contain non-empty strings, "
                    "each at most 400 characters."
                )
            quotes.append(entry.strip())
    return tuple(quotes)


def quote_for_day(quotes: tuple[str, ...], day: date) -> str:
    # A fixed daily seed preserves the selection across restarts.
    chooser = random.Random(day.isoformat())  # noqa: S311 - display text, not a secret
    return chooser.choice(quotes)


async def update_daily_bio(
    client: discord.Client, path: Path = Path("quotes.toml")
) -> None:
    updated_day: date | None = None
    while True:
        today = date.today()
        if client.is_ready() and today != updated_day:
            try:
                async with asyncio.timeout(30):
                    quotes = await asyncio.to_thread(load_quotes, path)
                    quote = quote_for_day(quotes, today)
                    application = await client.application_info()
                    if application.description != quote:
                        await application.edit(description=quote)
            except Exception as error:
                _LOGGER.warning(
                    "Daily bio update failed (%s); will retry.", type(error).__name__
                )
            else:
                updated_day = today
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
