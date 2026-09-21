# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio

import pytest

from nahormaar_backend.engine.cache import SnapshotCache


def test_versions_remain_stable_until_explicitly_selected_and_unchanged_refresh_reuses_version() -> (
    None
):
    async def scenario() -> None:
        now = 0.0
        cache = SnapshotCache[str, tuple[str, ...]](ttl=10, clock=lambda: now)
        value = ("first", "second", "first")
        calls = 0
        done = asyncio.Event()

        async def load() -> tuple[str, ...]:
            nonlocal calls
            calls += 1
            done.set()
            return value

        first = await cache.get("query", load)
        assert await cache.get("query", load) == first and calls == 1
        now = 11
        done.clear()
        assert await cache.get("query", load) == first
        assert cache.status(first.version).refreshing
        await done.wait()
        assert cache.status(first.version).latest_version == first.version
        value = ("new", "second", "first")
        now = 22
        done.clear()
        assert await cache.get("query", load) == first
        await done.wait()
        status = cache.status(first.version)
        assert status.latest_version != first.version and not status.refreshing
        assert cache.read(first.version).value == ("first", "second", "first")
        assert cache.read(status.latest_version).value == value
        assert calls == 3
        await cache.close()

    asyncio.run(scenario())


def test_shared_requests_survive_one_cancelled_waiter_and_close_owns_cleanup() -> None:
    async def scenario() -> None:
        cache = SnapshotCache[str, str](ttl=10)
        started, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = 0

        async def load() -> str:
            nonlocal calls
            calls += 1
            started.set()
            try:
                await release.wait()
                return "result"
            finally:
                cleaned.set()

        first = asyncio.create_task(cache.get("same", load))
        await started.wait()
        second = asyncio.create_task(cache.get("same", load))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not cleaned.is_set()
        release.set()
        assert (await second).value == "result" and calls == 1
        release.clear()
        started.clear()
        cleaned.clear()
        third = asyncio.create_task(cache.get("other", load))
        await started.wait()
        await cache.close()
        assert cleaned.is_set()
        with pytest.raises(asyncio.CancelledError):
            await third
        with pytest.raises(RuntimeError, match="closed"):
            await cache.get("same", load)

    asyncio.run(scenario())


@pytest.mark.parametrize("partial", [False, True])
def test_failed_refresh_keeps_complete_results_and_backs_off(partial: bool) -> None:
    async def scenario() -> None:
        now = 0.0
        cache = SnapshotCache[str, tuple[str, str | None]](
            ttl=300, clock=lambda: now, error=lambda value: value[1]
        )
        calls = 0

        async def load() -> tuple[str, str | None]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return "complete", None
            if partial:
                return "partial", "Some entries failed."
            raise ValueError("private upstream details")

        first = await cache.get("key", load)
        now = 301
        assert await cache.get("key", load) == first
        await asyncio.sleep(0)
        status = cache.status(first.version)
        assert status.latest_version == first.version and status.error
        assert "private" not in status.error
        assert await cache.get("key", load) == first and calls == 2
        now = 332
        assert await cache.get("key", load) == first
        await asyncio.sleep(0)
        assert calls == 3
        await cache.close()

    asyncio.run(scenario())


def test_cold_errors_are_retryable_and_capacity_versions_and_retention_are_bounded() -> (
    None
):
    async def scenario() -> None:
        now = 0.0
        cache = SnapshotCache[str, str](
            ttl=1, capacity=1, versions=2, retention=3, clock=lambda: now
        )

        async def failed() -> str:
            raise ValueError("failed")

        async def good() -> str:
            return "result"

        with pytest.raises(ValueError, match="failed"):
            await cache.get("one", failed)
        first = await cache.get("one", good)
        second = await cache.get("two", good)
        assert cache.read(first.version) == first
        await cache.get("three", good)
        with pytest.raises(ValueError, match="expired"):
            cache.read(first.version)
        now = 4
        with pytest.raises(ValueError, match="expired"):
            cache.read(second.version)
        await cache.close()

    asyncio.run(scenario())


def test_discovery_concurrency_is_bounded_without_dropping_cached_results() -> None:
    async def scenario() -> None:
        cache = SnapshotCache[str, str](ttl=1, max_pending=1)
        started, release = asyncio.Event(), asyncio.Event()

        async def load() -> str:
            started.set()
            await release.wait()
            return "result"

        pending = asyncio.create_task(cache.get("one", load))
        await started.wait()
        with pytest.raises(RuntimeError, match="busy"):
            await cache.get("two", load)
        release.set()
        await pending
        await cache.close()

    asyncio.run(scenario())
