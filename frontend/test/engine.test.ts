import { afterEach, describe, expect, it, vi } from "vitest";
import { presentSession } from "../shared/engine";
import { canControl, playbackPosition } from "../shared/player";
import { playing, session, occurrence, track } from "./engine-fixtures";

afterEach(() => vi.useRealTimers());
describe("engine presentation", () => {
	it("keeps logical play identity distinct from a retry/seek attempt", () => {
		const value = playing();
		const before = presentSession(value, null);
		value.playback.attempt_id = "retry";
		const after = presentSession(value, before);
		expect(after.playback_id).toBe(before.playback_id);
		expect(after.attempt_id).not.toBe(before.attempt_id);
		expect(after.current?.track_id).toBe(track.id);
	});
	it("does not rewind the clock on unrelated state changes and freezes when paused", () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date("2026-09-21T12:00:00Z"));
		const value = playing();
		value.checkpoint.position_seconds = 12;
		const before = presentSession(value, null);
		vi.advanceTimersByTime(2000);
		value.session.volume = 0.5;
		const after = presentSession(value, before);
		expect(playbackPosition(after, Date.now())).toBe(14);
		value.checkpoint.intent = "paused";
		value.checkpoint.position_seconds = 14;
		value.playback.phase = "paused";
		const paused = presentSession(value, after);
		vi.advanceTimersByTime(2000);
		expect(playbackPosition(paused, Date.now())).toBe(14);
	});
	it("does not mistake a desired reconnect channel for an active connection", () => {
		const value = playing();
		value.playback.phase = "suspended";
		value.playback.connection_id = null;
		value.playback.attempt_id = null;
		const state = presentSession(value, null);
		expect(state.channel_id).toBeTruthy();
		expect(state.voice_state).toBe("disconnected");
		expect(canControl(state, "skip")).toBe(false);
		expect(canControl(state, "play")).toBe(false);
	});
	it("uses history identities and track metadata for a failed-track notice", () => {
		const value = session();
		value.playback.error = "track_failed";
		value.history = [
			{
				...occurrence,
				id: "record",
				entry_id: occurrence.id,
				started_at: "2026-09-21T12:00:00Z",
				ended_at: "2026-09-21T12:01:00Z",
				end_reason: "failed",
			},
		];
		const state = presentSession(value, null);
		expect(state.last_issue?.entry?.title).toBe(track.metadata.title);
		expect(state.last_issue?.id).toBe("record");
		expect(state.recently_played[0]?.entry.id).toBe(occurrence.id);
	});
	it("preserves repeated queue occurrences and hides an ongoing history record", () => {
		const value = playing();
		value.queue = [occurrence, { ...occurrence, id: "second", position: 1 }];
		value.history = [
			{
				...occurrence,
				id: "play",
				entry_id: occurrence.id,
				started_at: "2026-09-21T12:00:00Z",
				ended_at: null,
				end_reason: null,
			},
		];
		const state = presentSession(value, null);
		expect(state.upcoming.map((item) => item.id)).toEqual([occurrence.id, "second"]);
		expect(state.recently_played).toEqual([]);
	});
});
