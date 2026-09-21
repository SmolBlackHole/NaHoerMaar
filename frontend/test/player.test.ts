import { session, playing, track as catalogTrack, occurrence, outcome } from "./engine-fixtures";
import type { SessionState } from "../shared/engine";
import { describe, expect, it, vi } from "vitest";
import { createPlayerClient } from "../app/player/client";
import {
	canControl,
	queueWaits,
	queueMoveTarget,
	formatWait,
	playbackPosition,
	youtubeVideoId,
	type PlayerState,
	type QueueEntry,
} from "../shared/player";

const track: QueueEntry = {
	track_id: "track",
	id: "c68fe9f1-ac72-4f15-9e7f-445d332b9ca7",
	source_url: "https://www.youtube.com/watch?v=Pqp9fDRp1lw",
	video_id: "Pqp9fDRp1lw",
	title: null,
	uploader: null,
	thumbnail_url: null,
	duration_seconds: 180,
	artist: null,
	uploader_url: null,
	added_by: null,
	origin: "manual",
};

describe("queue positions", () => {
	it("moves by entry identity to first, middle and last positions", () => {
		const ids = ["a", "b", "c", "d"];
		expect(queueMoveTarget(ids, "d", 1)).toBe("a");
		expect(queueMoveTarget(ids, "a", 3)).toBe("d");
		expect(queueMoveTarget(ids, "a", 4)).toBeNull();
		expect(queueMoveTarget(ids, "b", 2)).toBe("c");
		expect(ids).toEqual(["a", "b", "c", "d"]);
	});
	it("rejects invalid positions and entries without sending an end-of-queue move", () => {
		for (const position of [0, -1, 5, 1.5, NaN])
			expect(queueMoveTarget(["a", "b"], "a", position)).toBeUndefined();
		expect(queueMoveTarget(["a"], "removed", 1)).toBeUndefined();
	});
});
const state = (changes: Partial<PlayerState> = {}): PlayerState => ({
	session_id: "session",
	attempt_id: "attempt",
	revision: 1,
	queue_revision: 1,
	state: "idle",
	current: null,
	upcoming: [track],
	recently_played: [],
	voice_state: "connected",
	channel_id: "123456789012345678",
	playback_id: null,
	volume: 1,
	crossfade_seconds: 0,
	position_seconds: 0,
	position_updated_at: null,
	last_issue: null,
	radio: {
		state: "off",
		generation: null,
		title: null,
		seed: null,
		initiator: null,
		error: null,
	},
	...changes,
});

class Events extends EventTarget {
	onopen: (() => void) | null = null;
	onerror: (() => void) | null = null;
	close = vi.fn();
	emit(state: SessionState, action?: string) {
		this.dispatchEvent(
			new MessageEvent(action ? "change" : "state", {
				data: JSON.stringify(action ? { state, action, outcome: outcome() } : state),
			}),
		);
	}
}
function setup() {
	const events: Events[] = [];
	const mutation = vi.fn<typeof fetch>();
	const client = createPlayerClient(
		(url, options) =>
			url === "/api/channels" ? Promise.resolve(Response.json([])) : mutation(url, options),
		() => {
			const stream = new Events();
			events.push(stream);
			return stream as unknown as EventSource;
		},
	);
	client.connect();
	return { client, events, mutation };
}
describe("native engine client", () => {
	it("waits for resync, consumes changes and rejects stale state", () => {
		const { client, events } = setup();
		expect(client.enabled.value).toBe(false);
		const value = session();
		value.session.revision = 7;
		events[0]!.emit(value);
		expect(client.snapshot.value?.upcoming[0]?.title).toBe("Амура");
		expect(client.enabled.value).toBe(true);
		value.session.revision = 8;
		value.session.volume = 0.4;
		events[0]!.emit(value, "SetVolume");
		events[0]!.emit(session());
		expect(client.snapshot.value?.volume).toBe(0.4);
		events[0]!.onerror?.();
		expect(client.enabled.value).toBe(false);
		events[0]!.emit(value);
		expect(client.enabled.value).toBe(true);
		client.dispose();
	});
	it("accepts a new session with a lower revision and ignores replaced streams", () => {
		const { client, events } = setup();
		const value = session();
		value.session.revision = 99;
		events[0]!.emit(value);
		client.connect();
		const other = session();
		other.session.id = "fresh";
		events[1]!.emit(other);
		events[0]!.emit(value);
		expect(client.snapshot.value?.session_id).toBe("fresh");
		expect(events[0]!.close).toHaveBeenCalledOnce();
		client.dispose();
	});
	it("applies conflict snapshots without retrying rejected operations", async () => {
		const { client, events, mutation } = setup();
		events[0]!.emit(session());
		const next = session();
		next.session.queue_revision = 3;
		next.session.revision = 4;
		mutation.mockResolvedValue(
			Response.json(
				{ state: next, outcome: outcome({ code: "queue_conflict" }), replayed: false },
				{ status: 409 },
			),
		);
		expect(await client.move(occurrence.id, null, 1)).toBe(false);
		expect(mutation.mock.calls[0]![0]).toBe(`/api/queue/${occurrence.id}/position`);
		expect(mutation.mock.calls[0]![1]?.method).toBe("PUT");
		expect(client.snapshot.value?.queue_revision).toBe(3);
		expect(client.uncertain.value).toBeNull();
		client.dispose();
	});
	it("freezes the original attempt and idempotency key across a lost reply", async () => {
		const { client, events, mutation } = setup();
		events[0]!.emit(playing());
		mutation.mockRejectedValueOnce(new TypeError("lost"));
		await client.control("skip");
		expect(await client.control("skip")).toBe(false);
		const next = playing();
		next.session.revision = 2;
		next.playback.attempt_id = "next";
		events[0]!.emit(next);
		mutation.mockResolvedValue(
			Response.json({ state: next, outcome: outcome(), replayed: true }),
		);
		expect(await client.retry()).toBe(true);
		expect(mutation.mock.calls[1]![1]?.body).toBe(mutation.mock.calls[0]![1]?.body);
		expect(mutation.mock.calls[1]![1]?.headers).toEqual(mutation.mock.calls[0]![1]?.headers);
		expect(JSON.parse(String(mutation.mock.calls[1]![1]?.body))).toEqual({
			action: "skip",
			expected_attempt_id: "attempt",
		});
		client.dispose();
	});
	it("keeps seek bound to the attempt captured before dragging", async () => {
		const { client, events, mutation } = setup();
		events[0]!.emit(playing());
		mutation.mockResolvedValue(Response.json({ code: "playback_conflict" }, { status: 409 }));
		expect(await client.seek(75, "dragged-attempt")).toBe(false);
		expect(mutation.mock.calls[0]![0]).toBe("/api/playback/position");
		expect(JSON.parse(String(mutation.mock.calls[0]![1]?.body))).toEqual({
			seconds: 75,
			expected_attempt_id: "dragged-attempt",
		});
		client.dispose();
	});
	it("resolves links once and queues persistent IDs, keeping duplicate occurrences", async () => {
		const { client, events, mutation } = setup();
		events[0]!.emit(session());
		mutation
			.mockResolvedValueOnce(Response.json(catalogTrack))
			.mockResolvedValue(
				Response.json({
					state: session(),
					outcome: outcome({ added_count: 1, entries: [occurrence] }),
					replayed: false,
				}),
			);
		expect(await client.add(catalogTrack.source_url)).toBe(true);
		expect(mutation.mock.calls[0]![0]).toBe("/api/catalog/track");
		expect(JSON.parse(String(mutation.mock.calls[1]![1]?.body)).track_ids).toEqual([
			catalogTrack.id,
		]);
		await client.addMany([catalogTrack.id, catalogTrack.id]);
		expect(JSON.parse(String(mutation.mock.calls[2]![1]?.body)).track_ids).toEqual([
			catalogTrack.id,
			catalogTrack.id,
		]);
		client.dispose();
	});
	it("does not let a delayed reply roll back a newer event, and keeps removed titles", async () => {
		const { client, events, mutation } = setup();
		events[0]!.emit(session());
		let resolve!: (r: Response) => void;
		mutation.mockReturnValue(new Promise((done) => (resolve = done)));
		const pending = client.mutate(`/api/queue/${occurrence.id}`, "DELETE");
		const newer = session();
		newer.session.revision = 3;
		newer.session.volume = 0.8;
		newer.queue = [];
		newer.tracks = {};
		events[0]!.emit(newer);
		const older = session();
		older.session.revision = 2;
		older.queue = [];
		older.tracks = {};
		resolve(
			Response.json({
				state: older,
				outcome: outcome({ removed_count: 1, entries: [occurrence] }),
				replayed: false,
			}),
		);
		await pending;
		expect(client.snapshot.value?.volume).toBe(0.8);
		expect(client.completed.value?.result.entries[0]?.title).toBe("Амура");
		client.dispose();
	});
	it("invalidates in-flight link resolution on sign-out without queueing", async () => {
		const { client, events, mutation } = setup();
		events[0]!.emit(session());
		let resolve!: (r: Response) => void;
		mutation.mockReturnValue(new Promise((done) => (resolve = done)));
		const pending = client.add(catalogTrack.source_url);
		client.dispose();
		resolve(Response.json(catalogTrack));
		expect(await pending).toBe(false);
		expect(mutation).toHaveBeenCalledTimes(1);
		expect(client.snapshot.value).toBeNull();
	});
	it("keeps gateway failures uncertain and input rejection final", async () => {
		const { client, events, mutation } = setup();
		events[0]!.emit(session());
		mutation.mockResolvedValueOnce(Response.json({}, { status: 502 }));
		await client.addMany([catalogTrack.id]);
		expect(client.uncertain.value).not.toBeNull();
		mutation.mockResolvedValueOnce(Response.json({ code: "invalid_request" }, { status: 422 }));
		await client.retry();
		expect(client.uncertain.value).toBeNull();
		expect(client.error.value).toContain("invalid");
		client.dispose();
	});
});

describe("player presentation", () => {
	it("accounts for overlap in queue estimates, including short tracks", () => {
		const playing = state({
			state: "playing",
			current: { ...track, duration_seconds: 20 },
			crossfade_seconds: 5,
			position_seconds: 2,
			position_updated_at: "2026-09-20T12:00:00Z",
			upcoming: [
				{ ...track, duration_seconds: 4 },
				{ ...track, duration_seconds: 20 },
				{ ...track, duration_seconds: null },
				track,
			],
		});
		expect(queueWaits(playing, Date.parse("2026-09-20T12:00:00Z"))).toEqual([16, 18, 38, null]);
		expect(
			queueWaits({ ...playing, crossfade_seconds: 0 }, Date.parse("2026-09-20T12:00:00Z")),
		).toEqual([18, 22, 42, null]);
	});
	it("estimates starts in queue order and stops after an unknown duration", () => {
		const anchor = Date.parse("2026-09-19T12:00:00Z");
		const playing = state({
			state: "playing",
			current: track,
			position_seconds: 30,
			position_updated_at: new Date(anchor).toISOString(),
			upcoming: [track, { ...track, duration_seconds: null }, track],
		});
		expect(queueWaits(playing, anchor + 10_000)).toEqual([140, 320, null]);
		expect(
			queueWaits({ ...playing, current: { ...track, duration_seconds: null } }, anchor),
		).toEqual([null, null, null]);
		expect(queueWaits({ ...playing, state: "paused" }, anchor)).toEqual([null, null, null]);
		expect(queueWaits({ ...playing, voice_state: "disconnected" }, anchor)).toEqual([
			null,
			null,
			null,
		]);
		expect(queueWaits(null, anchor)).toEqual([]);
		expect(
			queueWaits(
				{ ...playing, last_issue: { code: "backend_halted", fatal: true, entry_id: null } },
				anchor,
			),
		).toEqual([null, null, null]);
		expect(formatWait(40)).toBe("In <1 min");
		expect(formatWait(140)).toBe("In ~2 min");
		expect(formatWait(3599)).toBe("In ~01:00 h");
		expect(formatWait(3600)).toBe("In ~01:00 h");
		expect(formatWait(4500)).toBe("In ~01:15 h");
		expect(formatWait(7200)).toBe("In ~02:00 h");
	});
	it("holds paused progress and clamps playing progress to the duration", () => {
		const anchor = "2026-09-19T12:00:00Z";
		const playing = state({
			state: "playing",
			current: track,
			position_seconds: 30,
			position_updated_at: anchor,
		});
		expect(playbackPosition(playing, Date.parse(anchor) + 5_000)).toBe(35);
		expect(playbackPosition({ ...playing, state: "paused" }, Date.parse(anchor) + 5_000)).toBe(
			30,
		);
		expect(playbackPosition(playing, Date.parse(anchor) + 300_000)).toBe(180);
	});
	it("offers only valid playback actions", () => {
		expect(canControl(state(), "play")).toBe(true);
		expect(canControl(state({ voice_state: "disconnected" }), "play")).toBe(false);
		expect(canControl(state({ upcoming: [] }), "play")).toBe(false);
		expect(canControl(state({ state: "loading", current: track }), "pause")).toBe(false);
		expect(canControl(state({ state: "loading", current: track }), "skip")).toBe(true);
		expect(canControl(state({ state: "error", current: track }), "play")).toBe(true);
	});
	it.each([
		"https://music.youtube.com/watch?v=Pqp9fDRp1lw",
		"https://youtu.be/Pqp9fDRp1lw",
		"https://www.youtube.com/shorts/Pqp9fDRp1lw",
	])("accepts individual YouTube links: %s", (url) => {
		expect(youtubeVideoId(url)).toBe("Pqp9fDRp1lw");
	});
	it.each([
		"some music",
		"https://youtube.com/playlist?list=123",
		"https://example.org/watch?v=Pqp9fDRp1lw",
	])("rejects unsupported sources: %s", (url) => {
		expect(youtubeVideoId(url)).toBeNull();
	});
});
