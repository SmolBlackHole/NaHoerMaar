# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Single-owner player service. Publish state only after its storage commit."""

from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID

from ..domain.commands import Receipt, Revisions
from ..domain.fsm import PlaybackEvent, VoiceEvent, transition, voice_transition
from ..domain.models import (
    HISTORY_LIMIT,
    HistoryEntry,
    PlayerSnapshot,
    QueueEntry,
    TrackMetadata,
)
from ..domain.undo import Removal
from ..persistence.player_store import SQLiteStore


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

    def removal(self, undo_id: UUID, actor_id: UUID | None) -> Removal:
        return self._store.removal(undo_id, actor_id)

    def restore_removal(self, removal: Removal) -> PlayerSnapshot:
        return self._commit(
            replace(self._snapshot, upcoming=removal.restore(self._snapshot.upcoming))
        )

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
        return self.enqueue_many((entry,))

    def enqueue_many(self, entries: tuple[QueueEntry, ...]) -> PlayerSnapshot:
        upcoming = self._snapshot.upcoming
        index = (
            next(
                (
                    index
                    for index, item in enumerate(upcoming)
                    if item.origin == "radio"
                ),
                len(upcoming),
            )
            if entries and all(item.origin == "manual" for item in entries)
            else len(upcoming)
        )
        return self._commit(
            replace(
                self._snapshot,
                upcoming=(*upcoming[:index], *entries, *upcoming[index:]),
            )
        )

    def enrich(self, entry_id: UUID, metadata: TrackMetadata) -> PlayerSnapshot:
        """Merge public metadata only while the entry still belongs to the queue."""
        values = {
            key: value for key, value in asdict(metadata).items() if value is not None
        }

        def enrich_entry(entry: QueueEntry) -> QueueEntry:
            return replace(entry, **values) if entry.id == entry_id else entry

        return self._commit(
            replace(
                self._snapshot,
                current=enrich_entry(self._snapshot.current)
                if self._snapshot.current
                else None,
                upcoming=tuple(
                    enrich_entry(entry) for entry in self._snapshot.upcoming
                ),
            )
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

    def clear(self, contributor_id: UUID | None = None) -> PlayerSnapshot:
        remaining = (
            tuple(
                entry
                for entry in self._snapshot.upcoming
                if entry.added_by is None or entry.added_by.id != contributor_id
            )
            if contributor_id is not None
            else ()
        )
        return self._commit(replace(self._snapshot, upcoming=remaining))

    def _apply(self, event: PlaybackEvent) -> PlayerSnapshot:
        return self._commit(transition(self._snapshot, event))

    def play(self) -> PlayerSnapshot:
        """Start the next entry, resume a paused entry, or retry a failed entry."""
        return self._apply(PlaybackEvent.PLAY)

    def mark_playing(self, *, record_history: bool = True) -> PlayerSnapshot:
        """Confirm that the current loading entry has started."""
        snapshot = transition(self._snapshot, PlaybackEvent.READY)
        if record_history and snapshot.current is not None:
            item = HistoryEntry(snapshot.current, datetime.now(UTC))
            snapshot = replace(
                snapshot,
                recently_played=(item, *snapshot.recently_played)[:HISTORY_LIMIT],
            )
        return self._commit(snapshot)

    def pause(self) -> PlayerSnapshot:
        return self._apply(PlaybackEvent.PAUSE)

    def seek(self) -> PlayerSnapshot:
        return self._apply(PlaybackEvent.SEEK)

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
