# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import uuid4

import pytest

from nahormaar_backend import cache as cache_module
from nahormaar_backend.application import catalog as module
from nahormaar_backend.application.catalog import MediaCatalog, PreviewState
from nahormaar_backend.application.search import SearchCatalog
from nahormaar_backend.cache import SnapshotCache
from nahormaar_backend.domain.catalog import SearchSource
from nahormaar_backend.integrations import discovery as extractor_module
from nahormaar_backend.integrations.processes import ProcessResult
from test_catalog import PLAYLIST, VIDEO, Runner, item
from test_commands import wait_for
from test_search import Provider


def test_search_refresh_keeps_pagination_pinned_and_failures_keep_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        clock = [1000.0]
        monkeypatch.setattr(cache_module, "monotonic", lambda: clock[0])
        provider = Provider("Original")
        catalog = SearchCatalog(
            {SearchSource.MUSIC: provider, SearchSource.VIDEOS: provider}
        )
        try:
            original = await catalog.search("song")
            clock[0] += 301
            provider.title = "Updated"
            provider.release = asyncio.Event()
            stale = await catalog.search("song")
            assert stale.refreshing and stale.entries == original.entries
            provider.release.set()
            await asyncio.sleep(0)
            second = await catalog.search("song", 10, snapshot_id=original.snapshot_id)
            assert second.entries[0].title == "Original"
            assert second.latest_snapshot_id != original.snapshot_id
            latest = await catalog.search("song", snapshot_id=second.latest_snapshot_id)
            assert latest.entries[0].title == "Updated"
            with pytest.raises(KeyError):
                await catalog.search("different", snapshot_id=original.snapshot_id)
            with pytest.raises(KeyError):
                await catalog.search(
                    "song", source=SearchSource.VIDEOS, snapshot_id=original.snapshot_id
                )
            clock[0] += 301
            provider.fail = True
            assert (await catalog.search("song")).entries == latest.entries
            await asyncio.sleep(0)
            failed = await catalog.search("song")
            assert failed.refresh_error and failed.snapshot_id == latest.snapshot_id
            assert len(provider.calls) == 3
            clock[0] += cache_module.SNAPSHOT_TTL
            with pytest.raises(KeyError):
                await catalog.search("song", 10, snapshot_id=original.snapshot_id)
        finally:
            await catalog.close()

    asyncio.run(scenario())


def test_playlist_shared_fetch_and_cancellation_are_separate_per_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        runner = Runner()
        runner.release = asyncio.Event()
        monkeypatch.setattr(extractor_module, "run_process", runner)
        catalog = MediaCatalog(Path("node"))
        first, second, owner, other = uuid4(), uuid4(), uuid4(), uuid4()
        try:
            catalog.start_preview(PLAYLIST, first, owner_id=owner)
            await runner.started.wait()
            catalog.start_preview(PLAYLIST, second, owner_id=other)
            await asyncio.sleep(0)
            await catalog.cancel_preview(first, owner_id=owner)
            assert not runner.cancelled
            assert (
                catalog.preview(first, owner_id=owner).state is PreviewState.CANCELLED
            )
            with pytest.raises(KeyError):
                catalog.preview(second, owner_id=owner)
            runner.release.set()
            await wait_for(
                lambda: (
                    catalog.preview(second, owner_id=other).state is PreviewState.READY
                )
            )
            assert len(runner.calls) == 1
            assert len(catalog.preview(second, owner_id=other).entries) == 1
        finally:
            await catalog.close()

    asyncio.run(scenario())


def test_playlist_refresh_preserves_complete_data_on_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        clock = [1000.0]
        monkeypatch.setattr(cache_module, "monotonic", lambda: clock[0])
        monkeypatch.setattr(module, "monotonic", lambda: clock[0])
        runner = Runner()
        runner.entries = [item(), item(id="bWHJbIm1TAA")]
        monkeypatch.setattr(extractor_module, "run_process", runner)
        catalog = MediaCatalog(Path("node"))
        owner = uuid4()
        try:
            catalog.start_preview(PLAYLIST, owner, owner_id=owner)
            await wait_for(
                lambda: (
                    catalog.preview(owner, owner_id=owner).state is PreviewState.READY
                )
            )
            first = catalog.preview(owner, owner_id=owner)
            clock[0] += 61
            runner.code = 1
            runner.entries = [item(title="Incomplete")]
            cached = catalog.start_preview(PLAYLIST, owner, owner_id=owner)
            assert cached.entries == first.entries and cached.refreshing
            await wait_for(
                lambda: not catalog.preview(owner, owner_id=owner).refreshing
            )
            failed = catalog.preview(owner, owner_id=owner)
            assert failed.entries == first.entries and failed.refresh_error
            assert failed.snapshot_id == first.snapshot_id
            clock[0] += 61
            runner.code = 0
            runner.entries = [item(id="bWHJbIm1TAA"), item(), item(id="dQw4w9WgXcQ")]
            catalog.start_preview(PLAYLIST, owner, owner_id=owner)
            await wait_for(
                lambda: not catalog.preview(owner, owner_id=owner).refreshing
            )
            updated = catalog.preview(owner, owner_id=owner)
            assert (
                len(updated.entries) == 3 and updated.snapshot_id != first.snapshot_id
            )
            assert updated.id == owner and not updated.refresh_error
        finally:
            await catalog.close()

    asyncio.run(scenario())


def test_link_metadata_is_immediate_and_refresh_is_coalesced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        clock = [1000.0]
        monkeypatch.setattr(cache_module, "monotonic", lambda: clock[0])
        monkeypatch.setattr(module, "monotonic", lambda: clock[0])
        runner = Runner()

        async def run(
            args: Sequence[str | os.PathLike[str]],
            *,
            timeout: float,
            on_stdout_line: Callable[[bytes], None] | None = None,
        ) -> ProcessResult:
            result = await runner(args, timeout=timeout, on_stdout_line=on_stdout_line)
            return ProcessResult(
                result.returncode, json.dumps(runner.entries[0]).encode(), b""
            )

        monkeypatch.setattr(extractor_module, "run_process", run)
        catalog = MediaCatalog(Path("node"))
        try:
            original = await catalog.metadata(VIDEO)
            clock[0] += 301
            runner.entries = [item(title="Fresh title")]
            runner.release = asyncio.Event()
            runner.started.clear()
            assert catalog.queue_entry(VIDEO, None).title == original.title
            first = asyncio.create_task(catalog.metadata(VIDEO))
            second = asyncio.create_task(
                catalog.metadata("https://music.youtube.com/watch?v=Pqp9fDRp1lw")
            )
            await runner.started.wait()
            assert catalog.queue_entry(VIDEO, None).title == original.title
            runner.release.set()
            assert (await first).title == (await second).title == "Fresh title"
            assert len(runner.calls) == 2
        finally:
            await catalog.close()

    asyncio.run(scenario())


def test_snapshot_storage_is_bounded_and_unchanged_refresh_keeps_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        cache = SnapshotCache[str](2, 0)

        async def load() -> str:
            return "same"

        original = await cache.get("a", load)
        clock = original.checked_at + cache_module.SNAPSHOT_TTL + 1
        monkeypatch.setattr(cache_module, "monotonic", lambda: clock)
        await cache.get("a", load)
        assert cache.version("a", original.id).value == "same"
        await wait_for(lambda: not cache.pending)
        refreshed = cache.peek("a")
        assert refreshed is not None and refreshed.id == original.id
        for index in range(30):
            cache.put(str(index), str(index))
        assert len(cache.values) == 2 and len(cache.versions) <= 6
        assert len(cache.attempts) <= 2 and len(cache.errors) <= 2
        await cache.close()

    asyncio.run(scenario())
