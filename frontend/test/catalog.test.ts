import { afterEach, describe, expect, it, vi } from "vitest";
import {
	musicSource,
	selectedSources,
	type CatalogTrack,
	type PlaylistPreview,
} from "../shared/catalog";
import { createCatalogClient } from "../app/player/catalog";

const video = "https://www.youtube.com/watch?v=Pqp9fDRp1lw";
const playlist = "https://www.youtube.com/playlist?list=PL12345678901234";
const entry = (index = 1, changes: Partial<CatalogTrack> = {}): CatalogTrack => ({
	index,
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
const preview = (state: PlaylistPreview["state"] = "loading"): PlaylistPreview => ({
	id: "preview",
	source_url: playlist,
	state,
	title: "Example",
	entries: [entry()],
	limit: 100,
	truncated: false,
	error: null,
});
function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((done) => {
		resolve = done;
	});
	return { promise, resolve };
}
afterEach(() => vi.useRealTimers());

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

describe("discovery client", () => {
	it("defaults to Music and discards its pending page when switching to Videos", async () => {
		const page = deferred<Response>();
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json({ entries: [entry()], next_offset: 10 }))
			.mockReturnValueOnce(page.promise)
			.mockResolvedValueOnce(
				Response.json({ entries: [entry(1, { title: "Video" })], next_offset: null }),
			);
		const client = createCatalogClient(request);
		await client.search("same query");
		expect(request.mock.calls[0]![0]).toBe(
			"/api/catalog/search?q=same%20query&offset=0&source=youtube_music",
		);
		const pending = client.loadMore();
		client.searchSource.value = "youtube";
		await client.search(client.query.value);
		expect(request.mock.calls[2]![0]).toBe(
			"/api/catalog/search?q=same%20query&offset=0&source=youtube",
		);
		page.resolve(Response.json({ entries: [entry(11)], next_offset: 20 }));
		await pending;
		expect(client.results.value.map((track) => track.title)).toEqual(["Video"]);
		expect(client.nextOffset.value).toBeNull();
		client.dispose();
	});
	it("requires reopening an expired ready preview before import", async () => {
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json(preview("ready")))
			.mockResolvedValueOnce(
				Response.json(
					{ detail: "This playlist preview expired. Open the playlist again." },
					{ status: 410 },
				),
			);
		const client = createCatalogClient(request);
		await client.openPreview(playlist);
		expect(await client.validatePreview()).toBe(false);
		expect(client.previewError.value).toContain("expired");
		client.dispose();
	});
	it("ignores stale search responses even when transport does not honor abort", async () => {
		const first = deferred<Response>();
		const second = deferred<Response>();
		const request = vi
			.fn<typeof fetch>()
			.mockReturnValueOnce(first.promise)
			.mockReturnValueOnce(second.promise);
		const client = createCatalogClient(request);
		const older = client.search("old");
		const newer = client.search("Амура");
		second.resolve(Response.json({ entries: [entry()], next_offset: 10 }));
		await newer;
		first.resolve(Response.json({ entries: [entry(2, { title: "Old" })], next_offset: null }));
		await older;
		expect(client.query.value).toBe("Амура");
		expect(client.results.value[0]?.title).toBe("Амура");
		expect(client.searching.value).toBe(false);
		expect(request.mock.calls[1]![0]).toContain(encodeURIComponent("Амура"));
		client.dispose();
	});
	it("keeps a closed search closed when its response arrives", async () => {
		const response = deferred<Response>();
		const client = createCatalogClient(() => response.promise);
		const searching = client.search("example");
		client.clearSearch();
		response.resolve(Response.json({ entries: [entry()], next_offset: 10 }));
		await searching;
		expect(client.query.value).toBe("");
		expect(client.results.value).toEqual([]);
		expect(client.nextOffset.value).toBeNull();
	});
	it("appends pages without duplicate videos and stops at the last page", async () => {
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json({ entries: [entry()], next_offset: 10 }))
			.mockResolvedValueOnce(
				Response.json({
					entries: [entry(11), entry(12, { video_id: "bWHJbIm1TAA" })],
					next_offset: null,
				}),
			);
		const client = createCatalogClient(request);
		await client.search("music");
		await client.loadMore();
		expect(client.results.value.map((item) => item.video_id)).toEqual([
			"Pqp9fDRp1lw",
			"bWHJbIm1TAA",
		]);
		expect(request.mock.calls[1]![0]).toContain("offset=10");
		await client.loadMore();
		expect(request).toHaveBeenCalledTimes(2);
		client.dispose();
	});
	it("keeps loaded results on a page failure and retries the same offset", async () => {
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json({ entries: [entry()], next_offset: 10 }))
			.mockRejectedValueOnce(new Error("Try again"))
			.mockResolvedValueOnce(Response.json({ entries: [], next_offset: null }));
		const client = createCatalogClient(request);
		await client.search("music");
		await client.loadMore();
		expect(client.results.value).toHaveLength(1);
		expect(client.searchError.value).toBe("Try again");
		expect(client.loadingMore.value).toBe(false);
		await client.loadMore();
		expect(request.mock.calls[2]![0]).toBe(request.mock.calls[1]![0]);
		expect(client.searchError.value).toBe("");
		client.dispose();
	});
	it("does not append an old page after a new query, even if abort is ignored", async () => {
		const page = deferred<Response>();
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json({ entries: [entry()], next_offset: 10 }))
			.mockReturnValueOnce(page.promise)
			.mockResolvedValueOnce(
				Response.json({
					entries: [entry(1, { video_id: "new", title: "New" })],
					next_offset: null,
				}),
			);
		const client = createCatalogClient(request);
		await client.search("old");
		const more = client.loadMore();
		await client.loadMore();
		expect(request).toHaveBeenCalledTimes(2);
		await client.search("new");
		page.resolve(Response.json({ entries: [entry(11)], next_offset: 20 }));
		await more;
		expect(client.results.value.map((item) => item.title)).toEqual(["New"]);
		expect(client.nextOffset.value).toBeNull();
		expect(client.loadingMore.value).toBe(false);
		client.dispose();
	});
	it("shows expiry and stops polling", async () => {
		vi.useFakeTimers();
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json(preview()))
			.mockResolvedValueOnce(
				Response.json({ detail: "Preview expired. Open it again." }, { status: 410 }),
			);
		const client = createCatalogClient(request);
		await client.openPreview(playlist);
		await vi.advanceTimersByTimeAsync(10_000);
		expect(client.previewError.value).toContain("expired");
		expect(request).toHaveBeenCalledTimes(2);
		request.mockResolvedValue(Response.json(preview("cancelled")));
		client.dispose();
	});
	it("does not let an in-flight poll undo cancellation", async () => {
		const progress = deferred<Response>();
		const request = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json(preview()))
			.mockReturnValueOnce(progress.promise)
			.mockResolvedValueOnce(Response.json(preview("cancelled")));
		const client = createCatalogClient(request);
		await client.openPreview(playlist);
		await client.cancelPreview();
		progress.resolve(Response.json(preview("ready")));
		await progress.promise;
		await Promise.resolve();
		expect(client.preview.value?.state).toBe("cancelled");
		client.dispose();
	});
	it("cancels a preview that was closed before its creation response arrived", async () => {
		const response = deferred<Response>();
		const request = vi
			.fn<typeof fetch>()
			.mockReturnValueOnce(response.promise)
			.mockImplementation(async () => Response.json(preview("cancelled")));
		const client = createCatalogClient(request);
		const opening = client.openPreview(playlist);
		const id = new Headers(request.mock.calls[0]![1]?.headers).get("idempotency-key");
		client.closePreview();
		response.resolve(Response.json(preview()));
		await opening;
		expect(client.preview.value).toBeNull();
		expect(client.previewUrl.value).toBe("");
		expect(
			request.mock.calls
				.slice(1)
				.every(
					([url, options]) =>
						url === `/api/youtube/playlists/${id}` && options?.method === "DELETE",
				),
		).toBe(true);
		expect(request).toHaveBeenCalledTimes(3);
	});
});
