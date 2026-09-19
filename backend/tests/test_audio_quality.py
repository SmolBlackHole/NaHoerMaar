# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

# pyright: reportPrivateUsage=false

import audioop
from io import BytesIO
import math
from pathlib import Path
import struct
import subprocess
import wave

import discord
from discord.oggparse import OggStream
import pytest

from nahormaar_backend.config import ffmpeg_executable
from nahormaar_backend.discord_voice import _FFmpegSource, _VolumeSource


def _opus_fixture(tmp_path: Path, frame_duration: int = 20) -> Path:
    wave_path = tmp_path / "stereo.wav"
    frames = bytearray()
    for sample_index in range(48_000):
        left = int(12_000 * math.sin(2 * math.pi * 440 * sample_index / 48_000))
        right = int(8_000 * math.sin(2 * math.pi * 660 * sample_index / 48_000))
        frames.extend(struct.pack("<hh", left, right))
    with wave.open(str(wave_path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(frames)
    opus_path = tmp_path / f"stereo-{frame_duration}.opus"
    subprocess.run(  # noqa: S603 - fixed local encoder and test fixture paths
        [
            str(ffmpeg_executable()),
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(wave_path),
            "-c:a",
            "libopus",
            "-b:a",
            "128k",
            "-frame_duration",
            str(frame_duration),
            str(opus_path),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    return opus_path


def _packets(path: Path) -> list[bytes]:
    return [
        packet
        for packet in OggStream(BytesIO(path.read_bytes())).iter_packets()
        if not packet.startswith((b"OpusHead", b"OpusTags"))
    ]


def test_unity_volume_preserves_every_opus_packet(tmp_path: Path) -> None:
    opus = _opus_fixture(tmp_path)
    expected = _packets(opus)
    source = _VolumeSource(_FFmpegSource(ffmpeg_executable(), str(opus), (), opus=True))
    try:
        actual = list(iter(source.read, b""))
        assert source.is_opus()
        assert actual == expected
        assert source._encoder is None
    finally:
        source.cleanup()
    assert source.original.closed


@pytest.mark.parametrize("position", [0.2, 0.8])
@pytest.mark.parametrize("container", ["opus", "webm"])
def test_seek_keeps_original_opus_packets_at_the_requested_position(
    tmp_path: Path, position: float, container: str
) -> None:
    opus = _opus_fixture(tmp_path)
    expected = _packets(opus)
    media = opus
    if container == "webm":
        media = tmp_path / "stereo.webm"
        subprocess.run(  # noqa: S603 - fixed local encoder and test fixture paths
            [
                str(ffmpeg_executable()),
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                str(opus),
                "-c:a",
                "copy",
                str(media),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
    source = _VolumeSource(
        _FFmpegSource(
            ffmpeg_executable(), str(media), (), opus=True, position_seconds=position
        )
    )
    try:
        actual = list(iter(source.read, b""))
        start = expected.index(actual[0])
        assert start * 0.02 == pytest.approx(position, abs=0.04)
        assert actual == expected[start:]
        assert source._encoder is None
    finally:
        source.cleanup()


def test_live_volume_changes_keep_position_and_restore_original_packets(
    tmp_path: Path,
) -> None:
    opus = _opus_fixture(tmp_path)
    expected = _packets(opus)
    source = _VolumeSource(_FFmpegSource(ffmpeg_executable(), str(opus), (), opus=True))
    process = source.original._process
    reference_decoder = discord.opus.Decoder()  # type: ignore[no-untyped-call]
    listener_decoder = discord.opus.Decoder()  # type: ignore[no-untyped-call]
    quiet_reference = bytearray()
    quiet_output = bytearray()
    try:
        for index, original in enumerate(expected):
            if index < 10 or index >= 40:
                volume = 1.0
            elif index < 25:
                volume = 0.3
            else:
                volume = 0.0
            source.volume = volume
            reference = reference_decoder.decode(original, fec=False)
            actual = source.read()
            heard = listener_decoder.decode(actual, fec=False)
            assert len(heard) == 3_840
            if volume == 1.0:
                assert actual == original
            elif 15 <= index < 25:
                quiet_reference.extend(reference)
                quiet_output.extend(heard)
            elif 30 <= index < 40:
                assert audioop.rms(heard, 2) <= 2
            assert source.original._process is process
        assert source.read() == b""
        ratio = audioop.rms(quiet_output, 2) / audioop.rms(quiet_reference, 2)
        assert ratio == pytest.approx(0.3, abs=0.01)
    finally:
        source.cleanup()


@pytest.mark.parametrize("frame_duration", [10, 40])
def test_other_opus_packet_durations_keep_all_samples_in_twenty_ms_frames(
    tmp_path: Path,
    frame_duration: int,
) -> None:
    opus = _opus_fixture(tmp_path, frame_duration)
    expected = _packets(opus)
    decoder = discord.opus.Decoder()  # type: ignore[no-untyped-call]
    total_input_bytes = sum(len(decoder.decode(p, fec=False)) for p in expected)
    source = _VolumeSource(_FFmpegSource(ffmpeg_executable(), str(opus), (), opus=True))
    output_decoder = discord.opus.Decoder()  # type: ignore[no-untyped-call]
    try:
        output = [output_decoder.decode(p, fec=False) for p in iter(source.read, b"")]
        assert all(len(frame) == 3_840 for frame in output)
        assert len(output) == math.ceil(total_input_bytes / 3_840)
        assert audioop.rms(b"".join(output), 2) > 1_000
    finally:
        source.cleanup()


def test_pcm_fallback_preserves_stereo_and_frame_timing(tmp_path: Path) -> None:
    _opus_fixture(tmp_path)
    source = _VolumeSource(
        _FFmpegSource(ffmpeg_executable(), str(tmp_path / "stereo.wav"), ())
    )
    decoder = discord.opus.Decoder()  # type: ignore[no-untyped-call]
    try:
        frames = [decoder.decode(p, fec=False) for p in iter(source.read, b"")]
        assert len(frames) == 50
        assert all(len(frame) == 3_840 for frame in frames)
        stereo = list(struct.iter_unpack("<hh", b"".join(frames[5:])))
        left_rms = math.sqrt(sum(left * left for left, _ in stereo) / len(stereo))
        right_rms = math.sqrt(sum(right * right for _, right in stereo) / len(stereo))
        assert left_rms / right_rms == pytest.approx(1.5, rel=0.03)
    finally:
        source.cleanup()
