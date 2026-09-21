# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from dataclasses import FrozenInstanceError, fields
from uuid import uuid4

import pytest

from nahormaar_backend.engine.audio import (
    AudioCompleted,
    AudioEndReason,
    AudioEvent,
    AudioProgress,
    AudioStarted,
    CrossfadeCompleted,
    CrossfadeDue,
    PlayableSource,
    VoiceConnection,
    VoiceDisconnected,
)
from nahormaar_backend.engine.domain.catalog import (
    MediaKind,
    MediaReference,
    TrackFinding,
)
from nahormaar_backend.engine.domain.metadata import TrackMetadata
from nahormaar_backend.engine.domain.tracks import MediaIdentity, Track


def test_resolved_source_keeps_private_transport_data_out_of_catalog_and_repr() -> None:
    finding = TrackFinding(
        MediaReference(
            MediaIdentity("youtube", "GCYGuZGE6DA"),
            MediaKind.TRACK,
            "https://www.youtube.com/watch?v=GCYGuZGE6DA",
        ),
        TrackMetadata(title="Resolved title", duration_seconds=123),
    )
    source = PlayableSource(
        finding,
        "https://stream.invalid/temporary?signature=private",
        (("Authorization", "private-value"),),
        is_opus=True,
    )
    assert source.track.metadata is finding.metadata
    assert source.is_opus
    assert "private" not in repr(source)
    assert "stream.invalid" not in repr(source)
    for value_type in (Track, TrackMetadata, TrackFinding):
        assert not {"stream_url", "headers"} & {f.name for f in fields(value_type)}
    with pytest.raises(FrozenInstanceError):
        setattr(source, "stream_url", "different")  # noqa: B010 - runtime freeze check


def test_audio_facts_keep_outgoing_incoming_and_preparation_identities_distinct() -> (
    None
):
    outgoing, incoming, preparation = uuid4(), uuid4(), uuid4()
    events: list[AudioEvent] = [
        CrossfadeDue(outgoing, preparation),
        AudioStarted(incoming, 0.02),
        AudioCompleted(outgoing, 180.02, AudioEndReason.NATURAL),
        CrossfadeCompleted(incoming, preparation),
    ]
    # Late outgoing completion is distinguishable from the new confirmed output.
    assert events[0] == CrossfadeDue(outgoing, preparation)
    assert events[1] != AudioStarted(outgoing, 0.02)
    assert events[2] != AudioCompleted(incoming, 180.02, AudioEndReason.NATURAL)
    assert events[3] != CrossfadeCompleted(incoming, uuid4())
    failed = AudioCompleted(
        incoming, 1.25, AudioEndReason.INTERRUPTED, RuntimeError("private source")
    )
    assert failed.reason is AudioEndReason.INTERRUPTED
    assert failed.error is not None
    assert "private source" not in repr(failed)


def test_disconnect_is_correlated_even_without_audio_or_when_rejoining_same_channel() -> (
    None
):
    first = VoiceConnection(uuid4(), 123)
    second = VoiceConnection(uuid4(), 123)
    assert VoiceDisconnected(first, None) != VoiceDisconnected(second, None)
    progress = AudioProgress(uuid4(), 42.5, paused=True, transitioning=True)
    disconnected = VoiceDisconnected(second, progress)
    assert disconnected.progress is progress
    assert progress.paused and progress.transitioning
    with pytest.raises(FrozenInstanceError):
        setattr(progress, "position_seconds", 0)  # noqa: B010 - runtime freeze check


@pytest.mark.parametrize("position", [-1, float("nan"), float("inf")])
def test_audio_measurements_cannot_introduce_invalid_progress(position: float) -> None:
    with pytest.raises(ValueError, match="Audio position"):
        AudioStarted(uuid4(), position)
    with pytest.raises(ValueError, match="Audio position"):
        AudioCompleted(uuid4(), position, AudioEndReason.STOPPED)
    with pytest.raises(ValueError, match="Audio position"):
        AudioProgress(uuid4(), position)
