# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import date
from pathlib import Path
import tomllib
from typing import Self, cast

import discord
import pytest

import nahormaar_backend.daily_bio as bio_module
from nahormaar_backend.daily_bio import load_quotes, quote_for_day, update_daily_bio


class FakeApplication:
    def __init__(self, description: str = "", *, fail_first: bool = False) -> None:
        self.description = description
        self.fail_first = fail_first
        self.attempts = 0
        self.edits: list[str] = []

    async def edit(self, *, description: str) -> "FakeApplication":
        self.attempts += 1
        if self.fail_first and self.attempts == 1:
            raise ConnectionError("synthetic failure containing secret data")
        self.description = description
        self.edits.append(description)
        return self


class FakeClient:
    def __init__(self, application: FakeApplication) -> None:
        self.application = application
        self.reads = 0
        self.ready = True

    def is_ready(self) -> bool:
        return self.ready

    async def application_info(self) -> FakeApplication:
        self.reads += 1
        return self.application

    async def wait_for_reads(self, count: int) -> None:
        async with asyncio.timeout(2):
            while self.reads < count:
                await asyncio.sleep(0.001)


def test_quote_languages_load_in_order_and_selection_survives_reload(
    tmp_path: Path,
) -> None:
    path = tmp_path / "quotes.toml"
    path.write_text(
        '[quotes]\nen = [" First quote "]\nde = ["Zweiter Spruch"]\n'
        'nl = ["Nog eentje dan."]\n',
        encoding="utf-8",
    )
    quotes = load_quotes(path)
    assert quotes == ("First quote", "Zweiter Spruch", "Nog eentje dan.")
    selected = quote_for_day(quotes, date(2026, 9, 19))
    assert selected in quotes
    assert quote_for_day(load_quotes(path), date(2026, 9, 19)) == selected


@pytest.mark.parametrize(
    "contents",
    [
        "",
        "quotes = []",
        "[quotes]",
        "[quotes]\nde = []",
        '[quotes]\nde = "Not an array"',
        '[quotes]\nde = [""]',
        '[quotes]\nde = ["   "]',
        '[quotes]\nde = ["A quote", 42]',
        '[quotes]\nde = ["' + "x" * 401 + '"]',
    ],
)
def test_invalid_quote_lists_are_rejected(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "quotes.toml"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError, match="quotes"):
        load_quotes(path)


def test_malformed_toml_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "quotes.toml"
    path.write_text('[quotes]\nde = ["Unclosed array"', encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_quotes(path)


def test_daily_updates_wait_for_readiness_and_restart_does_not_rewrite_the_bio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "quotes.toml"
    path.write_text('[quotes]\nen = ["First", "Second", "Third"]\n', encoding="utf-8")
    day = date(2026, 9, 19)

    class ClockDate(date):
        @classmethod
        def today(cls) -> Self:
            return cls(day.year, day.month, day.day)

    monkeypatch.setattr(bio_module, "date", ClockDate)
    monkeypatch.setattr(bio_module, "_CHECK_INTERVAL_SECONDS", 0.01)

    async def scenario() -> None:
        nonlocal day
        application = FakeApplication()
        client = FakeClient(application)
        client.ready = False
        task = asyncio.create_task(update_daily_bio(cast(discord.Client, client), path))
        try:
            await asyncio.sleep(0.03)
            assert client.reads == 0
            client.ready = True
            await client.wait_for_reads(1)
            assert len(application.edits) == 1
            await asyncio.sleep(0.03)
            assert client.reads == 1
            day = date(2026, 9, 20)
            await client.wait_for_reads(2)
            assert application.description == quote_for_day(load_quotes(path), day)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        edits = list(application.edits)
        restarted = asyncio.create_task(
            update_daily_bio(cast(discord.Client, client), path)
        )
        try:
            await client.wait_for_reads(3)
            assert application.edits == edits
        finally:
            restarted.cancel()
            with pytest.raises(asyncio.CancelledError):
                await restarted

    asyncio.run(scenario())


def test_failed_bio_update_retries_without_exposing_response_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "quotes.toml"
    path.write_text('[quotes]\nen = ["A quote"]\n', encoding="utf-8")
    monkeypatch.setattr(bio_module, "_CHECK_INTERVAL_SECONDS", 0.01)

    async def scenario() -> None:
        application = FakeApplication("Previous bio", fail_first=True)
        client = FakeClient(application)
        task = asyncio.create_task(update_daily_bio(cast(discord.Client, client), path))
        try:
            await client.wait_for_reads(1)
            assert application.description == "Previous bio"
            await client.wait_for_reads(2)
            assert application.description == "A quote"
            assert application.edits == ["A quote"]
            assert not task.done()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert "Daily bio update failed (ConnectionError)" in caplog.text
    assert "secret data" not in caplog.text
