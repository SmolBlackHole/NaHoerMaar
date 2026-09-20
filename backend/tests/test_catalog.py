# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest

from nahormaar_backend import catalog as module
from nahormaar_backend.audio import TrackError
from nahormaar_backend.catalog import PreviewState, MediaCatalog
from nahormaar_backend.search import CatalogBusy, SearchSource
from nahormaar_backend.processes import ProcessResult, run_process
from nahormaar_backend.youtube import playlist_id
from test_commands import wait_for

PLAYLIST = "https://music.youtube.com/playlist?list=PL12345678901234"
VIDEO = "https://www.youtube.com/watch?v=Pqp9fDRp1lw"


def item(**changes: object) -> dict[str, object]:
    return {
        "id": "Pqp9fDRp1lw",
        "title": "Амура - Я хочу любить",
        "duration": 180,
        "channel": "Artist",
        "playlist_title": "My playlist",
        **changes,
    }


class Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.entries: list[dict[str, object]] = [item()]
        self.release: asyncio.Event | None = None
        self.started = asyncio.Event()
        self.cancelled = False
        self.code = 0

    async def __call__(
        self,
        args: Sequence[str | os.PathLike[str]],
        *,
        timeout: float,
        max_output_bytes: int = 2 * 1024 * 1024,
        on_stdout_line: Callable[[bytes], None] | None = None,
    ) -> ProcessResult:
        del timeout, max_output_bytes
        self.calls.append(tuple(os.fspath(arg) for arg in args))
        self.started.set()
        if on_stdout_line:
            for entry in self.entries:
                on_stdout_line(json.dumps(entry, ensure_ascii=False).encode())
        try:
            if self.release is not None:
                await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return ProcessResult(
            self.code, json.dumps({"entries": self.entries}).encode(), b""
        )


@pytest.mark.parametrize(
    "url",
    [
        PLAYLIST,
        "https://youtu.be/Pqp9fDRp1lw?list=PL12345678901234",
        VIDEO + "&list=PL12345678901234",
    ],
)
def test_playlist_sources(url: str) -> None:
    assert playlist_id(url) == "PL12345678901234"


@pytest.mark.parametrize(
    "url",
    [
        VIDEO,
        "https://example.org/playlist?list=PL12345678901234",
        "https://youtube.com.evil.test/playlist?list=PL12345678901234",
        "https://u:p@youtube.com/playlist?list=PL12345678901234",
        PLAYLIST + "&list=PLduplicate12345",
        "https://youtube.com/feed?list=PL12345678901234",
    ],
)
def test_rejects_unsupported_playlist_sources(url: str) -> None:
    assert playlist_id(url) is None


def test_search_limits_metadata_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        runner = Runner()
        runner.entries = [
            item(),
            item(
                id="bWHJbIm1TAA",
                duration=None,
                thumbnails=[{"url": "https://i.ytimg.com/example.jpg"}],
            ),
            item(id="dQw4w9WgXcQ", availability="private"),
            item(id="aaaaaaaaaaa", is_live=True),
        ] + [item()] * 20
        monkeypatch.setattr(module, "run_process", runner)
        catalog = MediaCatalog(Path("node"))
        try:
            page = await catalog.search("  Амура  ", source=SearchSource.VIDEOS)
            found = page.entries
            assert len(found) == 10
            assert page.next_offset == 10
            assert found[0].title == "Амура - Я хочу любить"
            assert found[1].duration_seconds is None
            assert found[1].thumbnail_url == "https://i.ytimg.com/example.jpg"
            assert found[2].unavailable and found[3].unavailable
            assert runner.calls[0][-1] == "ytsearch100:Амура"
            assert "--flat-playlist" in runner.calls[0]
            assert "--format" not in runner.calls[0]
            entry = catalog.queue_entry(VIDEO, None)
            assert entry.title == found[0].title
            assert (await catalog.metadata(VIDEO)).duration_seconds == 180
            assert len(runner.calls) == 1
        finally:
            await catalog.close()

    asyncio.run(scenario())


def test_search_pages_keep_positions_and_stop_at_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        runner = Runner()
        runner.entries = [item(id=f"{index:011d}") for index in range(23)]
        monkeypatch.setattr(module, "run_process", runner)
        catalog = MediaCatalog(Path("node"))
        try:
            second = await catalog.search("example", 10, SearchSource.VIDEOS)
            assert [entry.index for entry in second.entries] == list(range(11, 21))
            assert second.entries[0].video_id == "00000000010"
            assert second.next_offset == 20
            assert runner.calls[-1][-1] == "ytsearch100:example"
            last = await catalog.search("example", 20, SearchSource.VIDEOS)
            assert len(last.entries) == 3 and last.next_offset is None
            empty = await catalog.search("example", 30, SearchSource.VIDEOS)
            assert not empty.entries and empty.next_offset is None
            runner.entries = [item()] * 120
            bounded = await catalog.search("another", 90, SearchSource.VIDEOS)
            assert len(bounded.entries) == 10 and bounded.next_offset is None
            assert runner.calls[-1][-1] == "ytsearch100:another"
            for offset in (-10, 1, 100):
                with pytest.raises(ValueError, match="offset"):
                    await catalog.search("example", offset)
        finally:
            await catalog.close()

    asyncio.run(scenario())


def test_playlist_progress_limit_and_idempotent_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        runner = Runner()
        runner.entries = [item()] * 101
        runner.release = asyncio.Event()
        monkeypatch.setattr(module, "run_process", runner)
        catalog = MediaCatalog(Path("node"))
        key = uuid4()
        try:
            assert (
                catalog.start_preview(PLAYLIST, key, owner_id=key).state
                is PreviewState.LOADING
            )
            await runner.started.wait()
            progress = catalog.preview(key, owner_id=key)
            assert len(progress.entries) == 100 and progress.truncated
            assert progress.title == "My playlist"
            assert [entry.index for entry in progress.entries] == list(range(1, 101))
            assert catalog.start_preview(PLAYLIST, key, owner_id=key) == progress
            assert len(runner.calls) == 1
            with pytest.raises(ValueError):
                catalog.start_preview(PLAYLIST + "different", key, owner_id=key)
            runner.release.set()
            await wait_for(
                lambda: catalog.preview(key, owner_id=key).state is PreviewState.READY
            )
            assert "--playlist-items" in runner.calls[0]
            assert "1:101" in runner.calls[0]
        finally:
            await catalog.close()

    asyncio.run(scenario())


def test_cancel_and_shutdown_reap_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        runner = Runner()
        runner.release = asyncio.Event()
        monkeypatch.setattr(module, "run_process", runner)
        catalog = MediaCatalog(Path("node"))
        key = uuid4()
        catalog.start_preview(PLAYLIST, key, owner_id=key)
        await runner.started.wait()
        stopped = await catalog.cancel_preview(key, owner_id=key)
        assert runner.cancelled and stopped.state is PreviewState.CANCELLED
        assert await catalog.cancel_preview(key, owner_id=key) == stopped
        second = uuid4()
        catalog.start_preview(PLAYLIST, second, owner_id=key)
        await asyncio.sleep(0)
        await catalog.close()
        with pytest.raises(KeyError):
            catalog.preview(second, owner_id=key)

    asyncio.run(scenario())


def test_partial_failure_keeps_loaded_tracks(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        runner = Runner()
        runner.code = 1
        monkeypatch.setattr(module, "run_process", runner)
        catalog = MediaCatalog(Path("node"))
        key = uuid4()
        try:
            catalog.start_preview(PLAYLIST, key, owner_id=key)
            await wait_for(
                lambda: (
                    catalog.preview(key, owner_id=key).state is not PreviewState.LOADING
                )
            )
            result = catalog.preview(key, owner_id=key)
            assert result.state is PreviewState.READY and result.error
            assert len(result.entries) == 1
            with pytest.raises(TrackError):
                await catalog.search("anything")
        finally:
            await catalog.close()

    asyncio.run(scenario())


def test_preview_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(module, "run_process", Runner())
        catalog = MediaCatalog(Path("node"))
        key = uuid4()
        try:
            catalog.start_preview(PLAYLIST, key, owner_id=key)
            await wait_for(
                lambda: catalog.preview(key, owner_id=key).state is PreviewState.READY
            )
            now = monotonic()
            monkeypatch.setattr(module, "monotonic", lambda: now + 601)
            with pytest.raises(KeyError):
                catalog.preview(key, owner_id=key)
        finally:
            await catalog.close()

    asyncio.run(scenario())


def test_preview_is_owned_by_the_authenticated_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(module, "run_process", Runner())
        catalog = MediaCatalog(Path("node"))
        key, owner, other = uuid4(), uuid4(), uuid4()
        try:
            catalog.start_preview(PLAYLIST, key, owner_id=owner)
            with pytest.raises(ValueError):
                catalog.start_preview(PLAYLIST, key, owner_id=other)
            with pytest.raises(KeyError):
                catalog.preview(key, owner_id=other)
            with pytest.raises(KeyError):
                await catalog.cancel_preview(key, owner_id=other)
            assert catalog.preview(key, owner_id=owner).state is PreviewState.LOADING
        finally:
            await catalog.close()

    asyncio.run(scenario())


def test_process_progress_arrives_before_exit() -> None:
    import sys

    async def scenario() -> None:
        lines: list[bytes] = []
        task = asyncio.create_task(
            run_process(
                (
                    sys.executable,
                    "-c",
                    "import time; print('first', flush=True); time.sleep(30)",
                ),
                timeout=40,
                on_stdout_line=lines.append,
            )
        )
        await wait_for(lambda: bool(lines))
        assert lines == [b"first"] and not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_discovery_concurrency_and_waiting_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        runner = Runner()
        runner.release = asyncio.Event()
        monkeypatch.setattr(module, "run_process", runner)
        catalog = MediaCatalog(Path("node"))
        tasks = [asyncio.create_task(catalog.search(str(index))) for index in range(8)]
        try:
            await wait_for(lambda: len(runner.calls) == 2)
            with pytest.raises(CatalogBusy):
                await catalog.search("one too many")
            assert len(runner.calls) == 2
            runner.release.set()
            results = await asyncio.gather(*tasks)
            assert len(results) == len(runner.calls) == 8
        finally:
            await catalog.close()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())
