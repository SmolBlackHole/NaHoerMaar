# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

# pyright: reportPrivateUsage=false
import audioop
import struct
import threading
from typing import cast

from nahoermaar.integrations.audio import (
    AudioFrame,
    BufferedAudio,
    CrossfadeSource,
    FrameEncoder,
    VolumeSource,
)


class _Frames:
    def __init__(self, value: int, count: int) -> None:
        self.frame = AudioFrame(struct.pack("<hh", value, value) * 960)
        self.remaining = count
        self.original = self
        self.current_error: Exception | None = None
        self.closed = False

    def read_frame(self) -> AudioFrame | None:
        if self.remaining:
            self.remaining -= 1
            return self.frame
        return None

    def cleanup(self) -> None:
        self.closed = True


class _PcmEncoder:
    def encode(self, frame: AudioFrame, volume: float) -> bytes:
        return audioop.mul(frame.pcm, 2, volume)


def _buffered(value: int) -> tuple[_Frames, BufferedAudio]:
    source = _Frames(value, 100)
    audio = BufferedAudio(cast(VolumeSource, source))
    assert audio.wait_ready(100)
    return source, audio


def test_immediate_activation_uses_the_staged_track_before_crossfade_is_due() -> None:
    outgoing, current = _buffered(12_000)
    incoming, prepared = _buffered(-12_000)
    mixer = CrossfadeSource(current, volume=1)
    mixer._encoder = cast(FrameEncoder, _PcmEncoder())
    faded = threading.Event()
    try:
        assert mixer.stage(
            prepared,
            seconds=7,
            duration=180,
            on_due=lambda: None,
        )
        assert not mixer.activate(faded.set)
        assert mixer.activate(faded.set, immediate=True)
        assert mixer.read() == incoming.frame.pcm
        assert faded.wait(1)
        assert outgoing.closed
        assert not incoming.closed
    finally:
        mixer.cleanup()
    assert incoming.closed
