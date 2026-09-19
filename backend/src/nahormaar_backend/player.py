# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Single-owner player service. Publish state only after its storage commit."""

from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from .commands import Receipt, Revisions
from .fsm import PlaybackEvent, VoiceEvent, transition, voice_transition
from .models import PlayerSnapshot, QueueEntry
from .storage import SQLiteStore


class Player:
    def __init__(self, store: SQLiteStore) -> None:
        self._store = store
        stored = store.load()
        self._snapshot: PlayerSnapshot = stored
        self._revisions = store.revisions()
        self._receipt: Receipt | None = None
        self._commit(transition(stored, PlaybackEvent.RECOVER))

    @property
    def revisions(self) -> Revisions:
        return self._revisions

    @property
    def snapshot(self) -> PlayerSnapshot:
        return self._snapshot

    def _commit(self, snapshot: PlayerSnapshot) -> PlayerSnapshot:
        if snapshot != self._snapshot:
            versions = Revisions(
                self._revisions.revision + 1,
                self._revisions.queue_revision
                + (
                    tuple(entry.id for entry in snapshot.upcoming)
                    != tuple(entry.id for entry in self._snapshot.upcoming)
                ),
            )
            self._store.save(snapshot, revisions=versions, receipt=self._receipt)
            self._revisions = versions
            self._snapshot = snapshot
        elif self._receipt is not None:
            self._store.finish(self._receipt)
        return self._snapshot

    def reserve(self, receipt: Receipt) -> Receipt | None:
        return self._store.reserve(receipt)

    def recover_requests(self) -> None:
        self._store.interrupt_requests()

    def apply_request(
        self, receipt: Receipt, operation: Callable[["Player"], PlayerSnapshot]
    ) -> PlayerSnapshot:
        """Commit a queue operation and its successful outcome together."""
        self._receipt = receipt
        try:
            return operation(self)
        finally:
            self._receipt = None

    def publish(
        self, previous_revision: int, changed: bool, receipt: Receipt | None = None
    ) -> Revisions:
        """Assign runtime-only changes a durable revision before publication."""
        if changed and self._revisions.revision == previous_revision:
            versions = replace(self._revisions, revision=previous_revision + 1)
            self._store.save(self._snapshot, revisions=versions, receipt=receipt)
            self._revisions = versions
        elif receipt is not None:
            self._store.finish(receipt)
        return self._revisions

    def enqueue(self, entry: QueueEntry) -> PlayerSnapshot:
        return self._commit(
            replace(self._snapshot, upcoming=(*self._snapshot.upcoming, entry))
        )

    def _upcoming_entry(self, entry_id: UUID) -> QueueEntry:
        if self._snapshot.current is not None and self._snapshot.current.id == entry_id:
            raise ValueError("The current entry is controlled by skip and stop.")

        for entry in self._snapshot.upcoming:
            if entry.id == entry_id:
                return entry

        raise KeyError(entry_id)

    def remove(self, entry_id: UUID) -> PlayerSnapshot:
        self._upcoming_entry(entry_id)

        return self._commit(
            replace(
                self._snapshot,
                upcoming=tuple(
                    entry for entry in self._snapshot.upcoming if entry.id != entry_id
                ),
            )
        )

    def move_before(
        self, entry_id: UUID, before_entry_id: UUID | None = None
    ) -> PlayerSnapshot:
        entry = self._upcoming_entry(entry_id)

        if before_entry_id is not None:
            self._upcoming_entry(before_entry_id)
        if entry_id == before_entry_id:
            return self._snapshot

        upcoming = [item for item in self._snapshot.upcoming if item.id != entry_id]
        index = len(upcoming)
        if before_entry_id is not None:
            index = next(
                index
                for index, item in enumerate(upcoming)
                if item.id == before_entry_id
            )
        upcoming.insert(index, entry)

        return self._commit(replace(self._snapshot, upcoming=tuple(upcoming)))

    def clear(self) -> PlayerSnapshot:
        return self._commit(replace(self._snapshot, upcoming=()))

    def _apply(self, event: PlaybackEvent) -> PlayerSnapshot:
        return self._commit(transition(self._snapshot, event))

    def play(self) -> PlayerSnapshot:
        """Start the next entry, resume a paused entry, or retry a failed entry."""
        return self._apply(PlaybackEvent.PLAY)

    def mark_playing(self) -> PlayerSnapshot:
        """Confirm that the current loading entry has started."""
        return self._apply(PlaybackEvent.READY)

    def pause(self) -> PlayerSnapshot:
        return self._apply(PlaybackEvent.PAUSE)

    def skip(self) -> PlayerSnapshot:
        return self._apply(PlaybackEvent.SKIP)

    def stop(self) -> PlayerSnapshot:
        return self._apply(PlaybackEvent.STOP)

    def fail(self) -> PlayerSnapshot:
        return self._apply(PlaybackEvent.FAIL)

    def finished(self) -> PlayerSnapshot:
        return self._apply(PlaybackEvent.FINISHED)

    def voice(self, event: VoiceEvent) -> PlayerSnapshot:
        return self._commit(voice_transition(self._snapshot, event))
