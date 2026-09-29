// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { createPinia, disposePinia, setActivePinia, type Pinia } from "pinia";
import { nextTick } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createBackendCore } from "../../app/core/bootstrap";
import { fixture } from "./fixture";

const piniaInstances: Pinia[] = [];
afterEach(() => piniaInstances.splice(0).forEach(disposePinia));

function libraryPage(page: number, snapshot = "library-snapshot") {
	return {
		items: [],
		page,
		page_count: 2,
		page_size: 1,
		snapshot,
		total: 2,
	};
}

describe("library core", () => {
	it("uses the generated Library request contract", async () => {
		const { client, fetcher } = fixture();
		fetcher.mockImplementation(async () => Response.json({ items: [] }));

		await client.library.tracks({
			page: 2,
			pageSize: 10,
			query: "Still Alive",
			reaction: "like",
			snapshot: "snapshot one",
		});
		await client.library.summaries(["track-one", "track-two"]);
		await client.library.setReaction("track/one", "dislike");
		await client.library.deleteReaction("track/one");
		await client.library.participants("track/one", {
			page: 3,
			pageSize: 8,
			reaction: "dislike",
			snapshot: "people one",
		});

		expect(fetcher.mock.calls.map(([url, options]) => [url, options?.method])).toEqual([
			[
				"/api/library/tracks?page=2&page_size=10&q=Still%20Alive&reaction=like&snapshot=snapshot%20one",
				"GET",
			],
			["/api/library/reactions?track_id=track-one&track_id=track-two", "GET"],
			["/api/library/tracks/track%2Fone/reaction", "PUT"],
			["/api/library/tracks/track%2Fone/reaction", "DELETE"],
			[
				"/api/library/tracks/track%2Fone/reactions?page=3&page_size=8&reaction=dislike&snapshot=people%20one",
				"GET",
			],
		]);
		expect(JSON.parse(String(fetcher.mock.calls[2]![1]?.body))).toEqual({ value: "dislike" });
	});

	it("keeps Library collection pages on one stable snapshot", async () => {
		const fetcher = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json(libraryPage(1)))
			.mockResolvedValueOnce(Response.json(libraryPage(2)));
		const core = createBackendCore({ fetch: fetcher });
		core.authority.replace("session-token", "restored");
		const workflow = core.workflows.library();

		await workflow.loadTracks({
			page: 1,
			pageSize: 1,
			query: "Still Alive",
			filters: { reaction: "like" },
			newSnapshot: true,
		});
		await workflow.loadTracks({
			page: 2,
			pageSize: 1,
			query: "Still Alive",
			filters: { reaction: "like" },
		});

		expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
			"/api/library/tracks?page=1&page_size=1&q=Still%20Alive&reaction=like",
			"/api/library/tracks?page=2&page_size=1&q=Still%20Alive&reaction=like&snapshot=library-snapshot",
		]);
		workflow.dispose();
	});

	it("uses one generated contract for personal playlist reads and mutations", async () => {
		const { client, fetcher } = fixture();
		fetcher.mockImplementation(async () => Response.json({ items: [] }));

		await client.library.playlists({ page: 2, pageSize: 25, query: "Road", snapshot: "list" });
		await client.library.playlist("playlist/one");
		await client.library.createPlaylist("Road trip");
		await client.library.importPlaylist("https://youtube.com/playlist?list=source", "Source");
		await client.library.renamePlaylist("playlist/one", "Night drive", 4);
		await client.library.detachPlaylistSource("playlist/one", 5);
		await client.library.duplicatePlaylist("playlist/one", 5, "Copy");
		await client.library.playlistEntries("playlist/one", {
			page: 3,
			pageSize: 10,
			query: "Still Alive",
			snapshot: "revision",
		});
		await client.library.addPlaylistEntries(
			"playlist/one",
			[
				{ track_id: "track-one", preferred_source_id: "source-one" },
				{ track_id: "track-one", preferred_source_id: null },
			],
			6,
		);
		await client.library.deletePlaylistEntry("playlist/one", "entry/one", 7);
		await client.library.undoPlaylistEntry("playlist/one", "undo-one", 8);
		await client.library.movePlaylistEntry("playlist/one", "entry-two", 0, 9);
		await client.library.queuePlaylist("playlist/one", 10, "operation-one");
		await client.library.deletePlaylist("playlist/one", 11);

		expect(fetcher.mock.calls.map(([url, options]) => [url, options?.method])).toEqual([
			["/api/library/playlists?page=2&page_size=25&q=Road&snapshot=list", "GET"],
			["/api/library/playlists/playlist%2Fone", "GET"],
			["/api/library/playlists", "POST"],
			["/api/library/playlists/imports", "POST"],
			["/api/library/playlists/playlist%2Fone", "PATCH"],
			["/api/library/playlists/playlist%2Fone/source", "DELETE"],
			["/api/library/playlists/playlist%2Fone/duplicate", "POST"],
			[
				"/api/library/playlists/playlist%2Fone/entries?page=3&page_size=10&q=Still%20Alive&snapshot=revision",
				"GET",
			],
			["/api/library/playlists/playlist%2Fone/entries", "POST"],
			["/api/library/playlists/playlist%2Fone/entries/entry%2Fone", "DELETE"],
			["/api/library/playlists/playlist%2Fone/entries/undo", "POST"],
			["/api/library/playlists/playlist%2Fone/entries/entry-two/position", "PUT"],
			["/api/library/playlists/playlist%2Fone/queue", "POST"],
			["/api/library/playlists/playlist%2Fone", "DELETE"],
		]);
		expect(JSON.parse(String(fetcher.mock.calls[3]![1]?.body))).toEqual({
			name: "Source",
			source_url: "https://youtube.com/playlist?list=source",
		});
		expect(JSON.parse(String(fetcher.mock.calls[8]![1]?.body))).toEqual({
			expected_revision: 6,
			tracks: [
				{ track_id: "track-one", preferred_source_id: "source-one" },
				{ track_id: "track-one", preferred_source_id: null },
			],
		});
		expect(JSON.parse(String(fetcher.mock.calls[10]![1]?.body))).toEqual({
			undo_id: "undo-one",
			expected_revision: 8,
		});
		expect(JSON.parse(String(fetcher.mock.calls[11]![1]?.body))).toEqual({
			position: 0,
			expected_revision: 9,
		});
		expect(new Headers(fetcher.mock.calls[12]![1]?.headers).get("Idempotency-Key")).toBe(
			"operation-one",
		);
	});

	it("keeps playlist and entry pages on independent snapshots", async () => {
		const fetcher = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json(libraryPage(1, "playlists-one")))
			.mockResolvedValueOnce(Response.json(libraryPage(2, "playlists-one")))
			.mockResolvedValueOnce(Response.json(libraryPage(1, "entries-one")))
			.mockResolvedValueOnce(Response.json(libraryPage(2, "entries-one")));
		const core = createBackendCore({ fetch: fetcher });
		core.authority.replace("session-token", "restored");
		const workflow = core.workflows.library();

		await workflow.loadPlaylists({ page: 1, pageSize: 1, newSnapshot: true });
		await workflow.loadPlaylists({ page: 2, pageSize: 1 });
		await workflow.loadEntries("playlist-one", { page: 1, pageSize: 1, newSnapshot: true });
		await workflow.loadEntries("playlist-one", { page: 2, pageSize: 1 });

		expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
			"/api/library/playlists?page=1&page_size=1",
			"/api/library/playlists?page=2&page_size=1&snapshot=playlists-one",
			"/api/library/playlists/playlist-one/entries?page=1&page_size=1",
			"/api/library/playlists/playlist-one/entries?page=2&page_size=1&snapshot=entries-one",
		]);
		workflow.dispose();
	});

	it("deduplicates summary reads, rolls back a rejected optimistic reaction and clears on logout", async () => {
		const pinia = createPinia();
		setActivePinia(pinia);
		piniaInstances.push(pinia);
		let failMutation = true;
		const fetcher = vi.fn<typeof fetch>(async (input, options) => {
			const url = String(input);
			if (url === "/api/auth/session")
				return Response.json({
					csrf: "session-token",
					discord_id: "discord-user",
					expires_at: "2026-09-30T00:00:00Z",
					profile_complete: true,
					role: "user",
					user_id: "user-id",
				});
			if (url === "/api/library/reactions?track_id=track-one")
				return Response.json({
					items: [{ track_id: "track-one", likes: 4, dislikes: 1, reaction: null }],
				});
			if (url === "/api/library/tracks/track-one/reaction" && options?.method === "PUT") {
				if (failMutation)
					return Response.json(
						{ code: "library_unavailable", retryable: true },
						{ status: 503 },
					);
				return Response.json({
					track_id: "track-one",
					likes: 5,
					dislikes: 1,
					reaction: "like",
				});
			}
			throw new Error(`Unexpected request: ${url}`);
		});
		const core = createBackendCore({ fetch: fetcher });
		const session = core.stores.useSessionStore();
		const library = core.stores.useLibraryStore();
		expect(await session.restore()).toBe(true);
		await nextTick();

		await library.load(["track-one", "track-one"]);
		expect(library.summary("track-one")).toMatchObject({
			likes: 4,
			dislikes: 1,
			reaction: null,
		});
		expect(
			fetcher.mock.calls.filter(([url]) => String(url).startsWith("/api/library/reactions")),
		).toHaveLength(1);

		const rejected = library.setReaction("track-one", "like");
		expect(library.summary("track-one")?.reaction).toBe("like");
		expect(await rejected).toBe(false);
		expect(library.summary("track-one")).toMatchObject({
			likes: 4,
			dislikes: 1,
			reaction: null,
		});
		expect(library.error).toBeTruthy();

		failMutation = false;
		expect(await library.setReaction("track-one", "like")).toBe(true);
		expect(library.summary("track-one")).toMatchObject({
			likes: 5,
			dislikes: 1,
			reaction: "like",
		});
		expect(library.revision).toBe(1);

		core.authority.lost("signed_out");
		await nextTick();
		expect(library.summary("track-one")).toBeNull();
		expect(library.revision).toBe(0);
	});

	it("publishes playlist invalidations and resets them with the account session", async () => {
		const pinia = createPinia();
		setActivePinia(pinia);
		piniaInstances.push(pinia);
		const fetcher = vi.fn<typeof fetch>(async (input) => {
			if (String(input) === "/api/auth/session")
				return Response.json({
					csrf: "session-token",
					discord_id: "discord-user",
					expires_at: "2026-09-30T00:00:00Z",
					profile_complete: true,
					role: "user",
					user_id: "user-id",
				});
			throw new Error(`Unexpected request: ${String(input)}`);
		});
		const core = createBackendCore({ fetch: fetcher });
		const session = core.stores.useSessionStore();
		const library = core.stores.useLibraryStore();
		expect(await session.restore()).toBe(true);
		await nextTick();

		expect(library.playlistRevision).toBe(0);
		library.invalidatePlaylists();
		expect(library.playlistRevision).toBe(1);

		core.authority.lost("signed_out");
		await nextTick();
		expect(library.playlistRevision).toBe(0);
	});
});
