# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Manually exercise Discord playback before the dashboard is available."""

import argparse
import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast

from nahormaar_backend.config import Settings
from nahormaar_backend.models import PlaybackState, QueueEntry, VoiceState
from nahormaar_backend.playback import PlaybackController
from nahormaar_backend.runtime import open_runtime


async def wait_for_playing(controller: PlaybackController) -> None:
    async with asyncio.timeout(90):
        while controller.snapshot.state is not PlaybackState.PLAYING:
            if controller.status.last_issue is not None:
                raise RuntimeError(controller.status.last_issue.message)
            await asyncio.sleep(0.1)


async def exercise_controls(controller: PlaybackController) -> None:
    await wait_for_playing(controller)
    await asyncio.sleep(3)
    print("Pause for 2 seconds")
    await controller.pause()
    await asyncio.sleep(2)
    print("Resume at 30% volume")
    await controller.set_volume(0.3)
    await controller.play()
    await asyncio.sleep(3)
    print("Skip to the next track")
    await controller.skip()
    await wait_for_playing(controller)
    await asyncio.sleep(3)
    print("Stop, then manually restart the same track")
    await controller.stop()
    await asyncio.sleep(2)
    await controller.set_volume(1.0)
    await controller.play()


async def run(channel_id: int | None, urls: list[str], controls: bool) -> None:
    settings = replace(
        Settings.from_env(), database_path=Path("data/live-test.sqlite3").resolve()
    )
    async with open_runtime(settings) as controller:
        if channel_id is None:
            for channel in controller.channels():
                print(
                    f"{channel.id}  {channel.name}  "
                    f"connect={channel.can_connect} speak={channel.can_speak}"
                )
            return
        if len(urls) < 2:
            raise ValueError("Supply at least two YouTube URLs for the live test.")
        # Each run exercises the supplied inputs with a separate test database.
        await controller.clear()
        for url in urls:
            await controller.enqueue(QueueEntry(url))
        await controller.connect(channel_id)
        await controller.play()
        if controls:
            await exercise_controls(controller)
        previous = None
        while True:
            status = controller.status
            if status != previous:
                entry = status.player.current
                print(f"{status.player.state}: {entry.id if entry else 'queue empty'}")
                if status.last_issue is not None:
                    print(f"Playback issue: {status.last_issue.message}")
                previous = status
            if status.last_issue is not None and status.last_issue.fatal:
                raise RuntimeError(status.last_issue.message)
            if status.player.voice_state is VoiceState.DISCONNECTED:
                raise RuntimeError(
                    "Voice disconnected; the current track was preserved."
                )
            if status.player.state is PlaybackState.IDLE:
                if status.last_issue is not None:
                    raise RuntimeError(status.last_issue.message)
                return
            await asyncio.sleep(0.2)


def error_messages(error: BaseException) -> tuple[str, ...]:
    if isinstance(error, BaseExceptionGroup):
        group = cast(BaseExceptionGroup[BaseException], error)
        return tuple(
            message for nested in group.exceptions for message in error_messages(nested)
        )
    return (str(error),)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", type=int)
    parser.add_argument("--controls", action="store_true")
    parser.add_argument("urls", nargs="*")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.channel, args.urls, args.controls))
    except KeyboardInterrupt:
        print("Playback stopped; resources closed.")
    except Exception as exc:
        message = "; ".join(dict.fromkeys(error_messages(exc)))
        parser.exit(1, f"Live test failed: {message}\n")


if __name__ == "__main__":
    main()
