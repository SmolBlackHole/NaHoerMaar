import { discovery, track } from "./engine-fixtures";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
	musicSource,
	selectedSources,
	selectedTrackIds,
	catalogPage,
	reconcileSelection,
	queuePresence,
	importCounts,
	catalogEntry,
	type CatalogTrack,
} from "../shared/catalog";
import { createCatalogClient } from "../app/player/catalog";
import type { PlayerState } from "../shared/player";

const video = "https://www.youtube.com/watch?v=Pqp9fDRp1lw";
const playlist = "https://www.youtube.com/playlist?list=PL12345678901234";
const entry = (index = 1, changes: Partial<CatalogTrack> = {}): CatalogTrack => ({
	index,
	track_id: "track",
	source_url: video,
	video_id: "Pqp9fDRp1lw",
	title: "Амура",
	artist: null,
	uploader: "Artist",
	uploader_url: null,
	thumbnail_url: null,
	duration_seconds: null,
	unavailable: null,
	...changes,
});
function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((done) => {
		resolve = done;
	});
	return { promise, resolve };
}
afterEach(() => vi.useRealTimers());

describe("duplicate awareness", () => {
	const state = {
		current: catalogEntry(entry()),
		upcoming: [
			catalogEntry(
				entry(2, { source_url: "https://youtu.be/bWHJbIm1TAA", video_id: "bWHJbIm1TAA" }),
			),
		],
	} as PlayerState;
	it("matches video IDs across Music, YouTube and short links without comparing titles", () => {
		expect(queuePresence("https://music.youtube.com/watch?v=Pqp9fDRp1lw&list=abc", state)).toBe(
			"Now playing",
		);
		expect(queuePresence("https://youtube.com/watch?v=bWHJbIm1TAA", state)).toBe(
			"Already queued",
		);
		expect(queuePresence("https://youtu.be/GCYGuZGE6DA", state)).toBeNull();
	});
	it("counts queue, current and batch duplicates only when explicitly enabled", () => {
		const urls = [
			video,
			"https://youtu.be/bWHJbIm1TAA",
			"https://youtu.be/GCYGuZGE6DA",
			"https://music.youtube.com/watch?v=GCYGuZGE6DA",
		];
		expect(importCounts(urls, state, false)).toEqual({ added: 4, skipped: 0 });
		expect(importCounts(urls, state, true)).toEqual({ added: 1, skipped: 3 });
		expect(urls).toHaveLength(4);
	});
});
describe("selection refresh", () => {
	it("matches unique songs after reorder, drops removed songs and leaves new songs unchecked", () => {
		const old = [
			entry(1, { video_id: "a" }),
			entry(2, { video_id: "b" }),
			entry(3, { video_id: "c" }),
		];
		const next = [
			entry(1, { video_id: "b" }),
			entry(2, { video_id: "new" }),
			entry(3, { video_id: "a" }),
		];
		expect([...reconcileSelection(old, next, new Set([1, 3]))]).toEqual([3]);
	});
	it("keeps duplicate occurrences separate and drops ambiguous partial selections", () => {
		const duplicates = [entry(1), entry(2)];
		expect([...reconcileSelection(duplicates, duplicates, new Set([1]))]).toEqual([]);
		expect([...reconcileSelection(duplicates, duplicates, new Set([1, 2]))]).toEqual([1, 2]);
		expect([
			...reconcileSelection(duplicates, [...duplicates, entry(3)], new Set([1, 2])),
		]).toEqual([]);
		expect(selectedSources(duplicates, new Set([1, 2]))).toEqual([video, video]);
	});
});
describe("music sources", () => {
	it("keeps mixed video/playlist links as single songs with an explicit playlist option", () => {
		for (const url of [
			video + "&list=PL12345678901234",
			"https://music.youtube.com/watch?v=Pqp9fDRp1lw&list=PL12345678901234",
			"youtu.be/Pqp9fDRp1lw?list=PL12345678901234",
		])
			expect(musicSource(url)).toEqual({ kind: "video", url: video, playlist });
		expect(musicSource(playlist)).toEqual({ kind: "playlist", url: playlist });
		expect(musicSource("  Амура Я хочу любить  ")).toEqual({
			kind: "search",
			query: "Амура Я хочу любить",
		});
	});
	it("rejects unsupported links and empty searches", () => {
		for (const value of [
			"",
			"   ",
			"https://example.org/watch?v=Pqp9fDRp1lw",
			"https://youtube.com.evil.test/playlist?list=PL12345678901234",
			"javascript:alert(1)",
			playlist + "&list=PLduplicate123",
			"x".repeat(201),
		])
			expect(musicSource(value)).toEqual({ kind: "invalid" });
	});
	it("preserves source order and duplicate songs while excluding unavailable entries", () => {
		const tracks = [
			entry(1),
			entry(2, { source_url: video + "2" }),
			entry(3),
			entry(4, { unavailable: "Private" }),
		];
		expect(selectedSources(tracks, new Set([4, 3, 1, 2]))).toEqual([video, video + "2", video]);
	});
});

describe("native discovery", () => {
	it("defaults to Music and discards old results when switching provider", async () => {
		const late = deferred<Response>();
		const request = vi
			.fn<typeof fetch>()
			.mockReturnValueOnce(late.promise)
			.mockResolvedValueOnce(Response.json(discovery("video")));
		const client = createCatalogClient(request);
		const pending = client.search("same query");
		client.searchSource.value = "youtube";
		await client.search("same query");
		late.resolve(Response.json(discovery("music")));
		await pending;
		expect(request.mock.calls[0]![0]).toBe(
			"/api/catalog/search?q=same%20query&provider=youtube_music&refresh=true",
		);
		expect(request.mock.calls[1]![0]).toContain("provider=youtube&refresh=true");
		expect(client.results.value[0]?.title).toBe("video");
		client.dispose();
	});
	it("pins pagination until refreshed results are accepted", async () => {
		vi.useFakeTimers();
		const old = discovery("old", {
			total: 2,
			refresh: { latest_version: "old", refreshing: true, error: null },
		});
		const second = discovery("old", { offset: 1, total: 2 });
		second.entries[0]!.position = 1;
		second.entries[0]!.finding.reference!.identity.external_id = "another";
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json(old))
			.mockResolvedValueOnce(
				Response.json(
					discovery("old", {
						refresh: { latest_version: "new", refreshing: false, error: null },
					}),
				),
			)
			.mockResolvedValueOnce(Response.json(discovery("new")))
			.mockResolvedValueOnce(Response.json(second));
		const client = createCatalogClient(request);
		await client.search("song");
		await vi.advanceTimersByTimeAsync(1000);
		expect(client.results.value[0]?.title).toBe("old");
		expect(client.searchUpdate.value?.snapshot_id).toBe("new");
		await client.loadMore();
		expect(request.mock.calls[3]![0]).toBe("/api/catalog/search/old?offset=1");
		expect(client.results.value).toHaveLength(2);
		client.applySearchUpdate();
		expect(client.results.value[0]?.title).toBe("new");
		client.dispose();
	});
	it("keeps displayed data when a version expires", async () => {
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json(discovery("old", { total: 2 })))
			.mockResolvedValueOnce(Response.json({ code: "not_found" }, { status: 404 }));
		const client = createCatalogClient(request);
		await client.search("song");
		await client.loadMore();
		expect(client.searchExpired.value).toBe(true);
		expect(client.results.value[0]?.title).toBe("old");
		client.dispose();
	});
	it("ignores delayed pagination after a new query", async () => {
		const late = deferred<Response>();
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json(discovery("old", { total: 2 })))
			.mockReturnValueOnce(late.promise)
			.mockResolvedValueOnce(Response.json(discovery("new")));
		const client = createCatalogClient(request);
		await client.search("old");
		const pending = client.loadMore();
		await client.search("new");
		late.resolve(Response.json(discovery("old")));
		await pending;
		expect(client.results.value.map((item) => item.title)).toEqual(["new"]);
		expect(client.loadingMore.value).toBe(false);
		client.dispose();
	});
	it("loads all bounded playlist occurrences without collapsing duplicates", async () => {
		const initial = discovery("list", { total: 2, title: "Playlist" });
		const full = {
			...initial,
			entries: [...initial.entries, { ...initial.entries[0]!, position: 1 }],
		};
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json(initial))
			.mockResolvedValueOnce(Response.json(full));
		const client = createCatalogClient(request);
		await client.openPreview(playlist);
		expect(request.mock.calls[0]![0]).toBe("/api/catalog/playlist");
		expect(request.mock.calls[1]![0]).toBe("/api/catalog/playlist/list?limit=100");
		expect(selectedTrackIds(client.preview.value!.entries, new Set([0, 1]))).toEqual([
			track.id,
			track.id,
		]);
		expect(client.preview.value?.title).toBe("Playlist");
		client.dispose();
	});
	it("retains a visible playlist until its update is accepted", async () => {
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json(discovery("old")))
			.mockResolvedValueOnce(Response.json(discovery("new")));
		const client = createCatalogClient(request);
		await client.openPreview(playlist);
		await client.openPreview(playlist, true);
		expect(client.preview.value?.id).toBe("old");
		expect(client.previewUpdate.value?.id).toBe("new");
		client.applyPreviewUpdate();
		expect(client.preview.value?.id).toBe("new");
		client.dispose();
	});
	it("polls playlist refresh and stages the new version", async () => {
		vi.useFakeTimers();
		const old = discovery("old", {
			title: "Playlist",
			refresh: { latest_version: "old", refreshing: true, error: null },
		});
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json(old))
			.mockResolvedValueOnce(
				Response.json(
					discovery("old", {
						refresh: { latest_version: "new", refreshing: false, error: null },
					}),
				),
			)
			.mockResolvedValueOnce(Response.json(discovery("new")));
		const client = createCatalogClient(request);
		await client.openPreview(playlist);
		await vi.advanceTimersByTimeAsync(1000);
		expect(client.preview.value?.id).toBe("old");
		expect(client.previewUpdate.value?.id).toBe("new");
		expect(client.previewUpdate.value?.title).toBe("Playlist");
		client.dispose();
	});
	it("cancels locally without creating or deleting a remote preview job", async () => {
		const late = deferred<Response>();
		const request = vi.fn<typeof fetch>().mockReturnValueOnce(late.promise);
		const client = createCatalogClient(request);
		const pending = client.openPreview(playlist);
		client.closePreview();
		late.resolve(Response.json(discovery()));
		await pending;
		expect(client.preview.value).toBeNull();
		expect(client.previewPending.value).toBe(false);
		expect(request).toHaveBeenCalledOnce();
		expect(request.mock.calls[0]![1]?.signal?.aborted).toBe(true);
		client.dispose();
	});
	it("keeps unavailable slots visible and unselectable", () => {
		const native = discovery();
		native.entries[0]!.track_id = null;
		native.entries[0]!.finding.reason = "Private";
		const entries = catalogPage(native).entries;
		expect(entries[0]?.unavailable).toBe("Private");
		expect(selectedTrackIds(entries, new Set([0]))).toEqual([]);
	});
});
