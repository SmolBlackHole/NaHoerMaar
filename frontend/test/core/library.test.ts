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
});
