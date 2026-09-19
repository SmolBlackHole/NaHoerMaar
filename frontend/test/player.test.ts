import { describe, expect, it, vi } from "vitest";
import { createPlayerClient } from "../app/player/client";
import {
	canControl,
	playbackPosition,
	youtubeVideoId,
	type PlayerState,
	type QueueEntry,
} from "../shared/player";

const track: QueueEntry = {
	id: "c68fe9f1-ac72-4f15-9e7f-445d332b9ca7",
	source_url: "https://www.youtube.com/watch?v=Pqp9fDRp1lw",
	video_id: "Pqp9fDRp1lw",
	title: null,
	uploader: null,
	thumbnail_url: null,
	duration_seconds: 180,
	artist: null,
	uploader_url: null,
};
const state = (changes: Partial<PlayerState> = {}): PlayerState => ({
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
	position_seconds: 0,
	position_updated_at: null,
	last_issue: null,
	...changes,
});

class Events extends EventTarget {
	onopen: (() => void) | null = null;
	onerror: (() => void) | null = null;
	close = vi.fn();
	emit(snapshot: PlayerState) {
		this.dispatchEvent(new MessageEvent("state", { data: JSON.stringify(snapshot) }));
	}
}

function setup() {
	const events: Events[] = [];
	const mutation = vi.fn<typeof fetch>();
	const fetcher: typeof fetch = (url, options) =>
		url === "/api/channels" ? Promise.resolve(Response.json([])) : mutation(url, options);
	const client = createPlayerClient(fetcher, () => {
		const stream = new Events();
		events.push(stream);
		return stream as unknown as EventSource;
	});
	client.connect();
	return { client, events, mutation };
}

describe("live player", () => {
	it("waits for a snapshot after reconnect and ignores older revisions", () => {
		const { client, events } = setup();
		events[0]!.onopen?.();
		expect(client.enabled.value).toBe(false);
		events[0]!.emit(state({ revision: 7 }));
		expect(client.enabled.value).toBe(true);
		events[0]!.emit(state({ revision: 6, volume: 0 }));
		expect(client.snapshot.value?.volume).toBe(1);
		events[0]!.onerror?.();
		expect(client.enabled.value).toBe(false);
		events[0]!.onopen?.();
		expect(client.enabled.value).toBe(false);
		events[0]!.emit(state({ revision: 8, volume: 0.4 }));
		expect(client.enabled.value).toBe(true);
		expect(client.snapshot.value?.volume).toBe(0.4);
	});

	it("ignores events from a replaced stream and closes on disposal", () => {
		const { client, events } = setup();
		client.connect();
		expect(events[0]!.close).toHaveBeenCalledOnce();
		events[0]!.emit(state({ revision: 99 }));
		expect(client.snapshot.value).toBeNull();
		events[1]!.emit(state());
		client.dispose();
		expect(events[1]!.close).toHaveBeenCalledOnce();
		expect(client.enabled.value).toBe(false);
	});

	it("applies a conflicting queue snapshot and preserves the action's original revision", async () => {
		const { client, events, mutation } = setup();
		events[0]!.emit(state({ revision: 3, queue_revision: 2 }));
		mutation.mockResolvedValue(
			Response.json(
				{ code: "queue_conflict", snapshot: state({ revision: 4, queue_revision: 3 }) },
				{ status: 409 },
			),
		);
		expect(await client.move(track.id, null, 2)).toBe(false);
		expect(JSON.parse(mutation.mock.calls[0]![1]!.body as string).expected_queue_revision).toBe(
			2,
		);
		expect(client.snapshot.value?.queue_revision).toBe(3);
		expect(client.error.value).toContain("queue changed");
		expect(client.uncertain.value).toBeNull();
	});

	it("retains idempotency key and playback target when a response is lost", async () => {
		const { client, events, mutation } = setup();
		events[0]!.emit(state({ state: "playing", current: track, playback_id: "first-playback" }));
		mutation.mockRejectedValueOnce(new TypeError("network lost"));
		await client.control("skip");
		expect(client.enabled.value).toBe(false);
		expect(await client.control("skip")).toBe(false);
		expect(mutation).toHaveBeenCalledTimes(1);
		events[0]!.emit(state({ revision: 2, playback_id: "next-playback" }));
		mutation.mockResolvedValue(
			Response.json({ code: "ok", replayed: true, snapshot: state({ revision: 2 }) }),
		);
		expect(await client.retry()).toBe(true);
		const first = mutation.mock.calls[0]![1]!;
		const second = mutation.mock.calls[1]![1]!;
		expect(second.headers).toEqual(first.headers);
		expect(second.body).toEqual(first.body);
		expect(JSON.parse(second.body as string).expected_playback_id).toBe("first-playback");
		expect(client.enabled.value).toBe(true);
	});

	it("does not let a late HTTP reply overwrite a newer SSE snapshot", async () => {
		const { client, events, mutation } = setup();
		events[0]!.emit(state());
		let resolve!: (reply: Response) => void;
		mutation.mockReturnValue(
			new Promise<Response>((done) => {
				resolve = done;
			}),
		);
		const action = client.mutate("/api/player/volume", "PUT", { volume: 0.3 });
		expect(client.pending.value).toBe(true);
		events[0]!.emit(state({ revision: 3, volume: 0.8 }));
		resolve(Response.json({ code: "ok", snapshot: state({ revision: 2, volume: 0.3 }) }));
		await action;
		expect(client.snapshot.value?.volume).toBe(0.8);
	});

	it("keeps failed gateway requests retryable but treats rejected input as final", async () => {
		const { client, events, mutation } = setup();
		events[0]!.emit(state());
		mutation.mockResolvedValueOnce(Response.json({}, { status: 502 }));
		await client.mutate("/api/queue", "POST", { source_url: "bad" });
		expect(client.uncertain.value).not.toBeNull();
		mutation.mockResolvedValueOnce(Response.json({ detail: [] }, { status: 422 }));
		await client.retry();
		expect(client.uncertain.value).toBeNull();
		expect(client.error.value).toContain("single YouTube video");
	});

	it("blocks controls when offline or the backend is halted", async () => {
		const { client, events, mutation } = setup();
		expect(await client.control("play")).toBe(false);
		events[0]!.emit(
			state({ last_issue: { code: "backend_halted", fatal: true, entry_id: null } }),
		);
		expect(await client.control("play")).toBe(false);
		expect(mutation).not.toHaveBeenCalled();
	});
});

describe("player presentation", () => {
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
