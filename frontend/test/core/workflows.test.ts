// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { describe, expect, it, vi } from "vitest";
import { createBackendCore } from "../../app/core/bootstrap";
import type { Discovery } from "../../app/core/models/catalog";

describe("new backend page workflows", () => {
	it("discards a response that finishes after the account changes", async () => {
		let finishRead!: (response: Response) => void;
		const fetcher = vi
			.fn<typeof fetch>()
			.mockImplementation(() => new Promise<Response>((resolve) => (finishRead = resolve)));
		const core = createBackendCore({ fetch: fetcher });
		core.authority.replace("session-token", "restored");
		const recent = core.workflows.recent();
		const pending = recent.load();
		const signal = fetcher.mock.calls[0]![1]?.signal;
		expect(fetcher.mock.calls[0]![0]).toBe("/api/listening/recent?limit=20");

		core.authority.lost("signed_out");
		expect(signal?.aborted).toBe(true);
		finishRead(Response.json([]));
		expect(await pending).toBeNull();
		expect(recent.recent.data.value).toBeNull();
		expect(recent.recent.loading.value).toBe(false);
		recent.dispose();
	});

	it("merges discovery pages by source position and keeps the latest duplicate", async () => {
		const fetcher = vi.fn<typeof fetch>();
		const core = createBackendCore({ fetch: fetcher });
		core.authority.replace("session-token", "restored");
		fetcher
			.mockResolvedValueOnce(Response.json(discovery([0, 1], 1)))
			.mockResolvedValueOnce(Response.json(discovery([1, 2], null)));
		const workflow = core.workflows.discovery();

		await workflow.search("ambient");
		await workflow.more();

		expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
			"/api/catalog/search?q=ambient",
			"/api/catalog/search/version-id?offset=1&limit=20",
		]);
		expect(workflow.results.data.value?.entries.map(({ position }) => position)).toEqual([
			0, 1, 2,
		]);
		expect(workflow.results.data.value?.entries[1]?.track.title).toBe("page-two-1");
		workflow.dispose();
	});
});

function discovery(positions: number[], nextOffset: number | null): Discovery {
	return {
		kind: "search",
		version: "version-id",
		entries: positions.map((position) => ({
			position,
			source: {} as Discovery["entries"][number]["source"],
			track: {
				id: `track-${position}`,
				title: `page-${nextOffset === null ? "two" : "one"}-${position}`,
			} as Discovery["entries"][number]["track"],
		})),
		expires_at: "2026-09-26T00:00:00Z",
		fetched_at: "2026-09-25T00:00:00Z",
		next_offset: nextOffset,
		offset: nextOffset === null ? 1 : 0,
		playlist_title: null,
		provider: "youtube_music",
		query: "ambient",
		refreshing: false,
		source_has_more: nextOffset !== null,
		source_url: null,
		stale: false,
		total: 3,
	};
}
