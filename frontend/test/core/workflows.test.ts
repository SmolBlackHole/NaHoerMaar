// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { describe, expect, it, vi } from "vitest";
import { createBackendCore } from "../../app/core/bootstrap";
import type { Discovery } from "../../app/core/models/catalog";

describe("new backend page workflows", () => {
	it("keeps the newest profile period when an older request finishes later", async () => {
		const responses: ((response: Response) => void)[] = [];
		const fetcher = vi.fn<typeof fetch>(
			() => new Promise<Response>((resolve) => responses.push(resolve)),
		);
		const core = createBackendCore({ fetch: fetcher });
		core.authority.replace("session-token", "restored");
		const profile = core.workflows.profile();

		const older = profile.loadMine("7d");
		const oldSignal = fetcher.mock.calls[0]![1]?.signal;
		const newer = profile.loadMine("30d");
		expect(oldSignal?.aborted).toBe(true);
		responses[1]!(Response.json({ id: "newer-period" }));
		expect((await newer)?.id).toBe("newer-period");
		responses[0]!(Response.json({ id: "older-period" }));
		expect(await older).toBeNull();
		expect(profile.profile.data.value?.id).toBe("newer-period");
		expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
			"/api/profiles/me?period=7d",
			"/api/profiles/me?period=30d",
		]);
		profile.dispose();
	});

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
		expect(fetcher.mock.calls[0]![0]).toBe("/api/listening/recent?page=1&page_size=20");

		core.authority.lost("signed_out");
		expect(signal?.aborted).toBe(true);
		finishRead(
			Response.json({
				entries: [],
				page: 1,
				page_size: 20,
				total: 0,
				page_count: 0,
				snapshot: null,
			}),
		);
		expect(await pending).toBeNull();
		expect(recent.history.data.value).toBeNull();
		expect(recent.history.loading.value).toBe(false);
		recent.dispose();
	});

	it("keeps numbered history pages on one stable snapshot", async () => {
		const fetcher = vi.fn<typeof fetch>();
		const core = createBackendCore({ fetch: fetcher });
		core.authority.replace("session-token", "restored");
		fetcher
			.mockResolvedValueOnce(
				Response.json({
					entries: [{ playback_id: "first" }],
					page: 1,
					page_size: 1,
					total: 2,
					page_count: 2,
					snapshot: "snapshot-one",
				}),
			)
			.mockResolvedValueOnce(
				Response.json({
					entries: [{ playback_id: "second" }],
					page: 2,
					page_size: 1,
					total: 2,
					page_count: 2,
					snapshot: "snapshot-one",
				}),
			)
			.mockResolvedValueOnce(
				Response.json({
					entries: [{ playback_id: "radio" }],
					contributors: [],
					page: 1,
					page_size: 1,
					total: 1,
					page_count: 1,
					snapshot: "snapshot-two",
				}),
			);
		const workflow = core.workflows.recent();

		await workflow.load({ pageSize: 1, query: "Still Alive", newSnapshot: true });
		await workflow.load({ page: 2, pageSize: 1, query: "Still Alive" });
		await workflow.load({
			pageSize: 1,
			query: "Still Alive",
			filters: { radio: true, requestedBy: "user-one" },
		});

		expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
			"/api/listening/recent?page=1&page_size=1&q=Still+Alive",
			"/api/listening/recent?page=2&page_size=1&q=Still+Alive&snapshot=snapshot-one",
			"/api/listening/recent?page=1&page_size=1&q=Still+Alive&radio=true&requested_by=user-one",
		]);
		expect(workflow.history.data.value?.entries.map(({ playback_id }) => playback_id)).toEqual([
			"radio",
		]);
		expect(workflow.history.data.value?.page).toBe(1);
		workflow.dispose();
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

	it("continues the provider after every persisted snapshot entry is visible", async () => {
		const fetcher = vi.fn<typeof fetch>();
		const core = createBackendCore({ fetch: fetcher });
		core.authority.replace("session-token", "restored");
		fetcher
			.mockResolvedValueOnce(Response.json({ ...discovery([0, 1], null, true), total: 2 }))
			.mockResolvedValueOnce(
				Response.json({
					...discovery([2], null),
					version: "continued-version",
					offset: 2,
					total: 3,
				}),
			);
		const workflow = core.workflows.discovery();

		await workflow.search("ambient");
		await workflow.more();

		expect(fetcher.mock.calls.map(([url, options]) => [url, options?.method])).toEqual([
			["/api/catalog/search?q=ambient", undefined],
			["/api/catalog/search/version-id/continue?offset=2&limit=20", "POST"],
		]);
		expect(workflow.results.data.value?.version).toBe("continued-version");
		expect(workflow.results.data.value?.entries.map(({ position }) => position)).toEqual([
			0, 1, 2,
		]);
		expect(workflow.results.data.value?.source_has_more).toBe(false);
		workflow.dispose();
	});

	it("replaces the catalog job with the accepted manual run", async () => {
		const fetcher = vi.fn<typeof fetch>();
		const core = createBackendCore({ fetch: fetcher });
		core.authority.replace("session-token", "restored");
		const idle = {
			id: "catalog-maintenance",
			label: "Catalog maintenance",
			running: false,
			health: "unknown",
		};
		fetcher
			.mockResolvedValueOnce(Response.json({ jobs: [idle], history_retention_days: 30 }))
			.mockResolvedValueOnce(Response.json({ entries: [], next_cursor: null }))
			.mockResolvedValueOnce(
				Response.json({ ...idle, running: true, active_batch_size: 25 }),
			);
		const workflow = core.workflows.jobs();

		await workflow.load();
		await workflow.runCatalogMaintenance(25);

		expect(workflow.jobs.data.value?.jobs[0]).toMatchObject({
			id: "catalog-maintenance",
			running: true,
			active_batch_size: 25,
		});
		expect(fetcher.mock.calls.map(([url, options]) => [url, options?.method])).toEqual([
			["/api/jobs", undefined],
			["/api/jobs/runs?limit=20", undefined],
			["/api/jobs/catalog-maintenance", "POST"],
		]);
		workflow.dispose();
	});

	it("starts housekeeping and keeps the persisted history projection", async () => {
		const fetcher = vi.fn<typeof fetch>();
		const core = createBackendCore({ fetch: fetcher });
		core.authority.replace("session-token", "restored");
		const idle = {
			id: "housekeeping",
			label: "Data housekeeping",
			running: false,
			health: "healthy",
		};
		const recentRun = {
			id: "run-one",
			job_id: "housekeeping",
			status: "succeeded",
			details: [{ label: "Expired sessions", outcome: "changed" }],
		};
		const runSummary = {
			id: recentRun.id,
			job_id: recentRun.job_id,
			status: recentRun.status,
		};
		fetcher
			.mockResolvedValueOnce(
				Response.json({
					jobs: [idle],
					history_retention_days: 30,
				}),
			)
			.mockResolvedValueOnce(Response.json({ entries: [runSummary], next_cursor: null }))
			.mockResolvedValueOnce(
				Response.json({ ...idle, running: true, active_batch_size: 500 }),
			)
			.mockResolvedValueOnce(Response.json(recentRun));
		const workflow = core.workflows.jobs();

		await workflow.load();
		await workflow.runHousekeeping(500);
		await workflow.loadRun("run-one");
		await workflow.loadRun("run-one");

		expect(workflow.jobs.data.value?.jobs[0]).toMatchObject({
			id: "housekeeping",
			running: true,
			active_batch_size: 500,
		});
		expect(workflow.runs.data.value?.entries).toEqual([runSummary]);
		expect(workflow.runDetail("run-one").data.value?.details).toEqual(recentRun.details);
		expect(fetcher.mock.calls.map(([url, options]) => [url, options?.method])).toEqual([
			["/api/jobs", undefined],
			["/api/jobs/runs?limit=20", undefined],
			["/api/jobs/housekeeping", "POST"],
			["/api/jobs/runs/run-one", undefined],
		]);
		workflow.dispose();
	});

	it("keeps loaded job history pages while refreshing the newest summaries", async () => {
		const fetcher = vi.fn<typeof fetch>();
		const core = createBackendCore({ fetch: fetcher });
		core.authority.replace("session-token", "restored");
		const status = { jobs: [], history_retention_days: 30 };
		fetcher
			.mockResolvedValueOnce(Response.json(status))
			.mockResolvedValueOnce(
				Response.json({ entries: [{ id: "run-two" }], next_cursor: "older" }),
			)
			.mockResolvedValueOnce(
				Response.json({ entries: [{ id: "run-one" }], next_cursor: null }),
			)
			.mockResolvedValueOnce(Response.json(status))
			.mockResolvedValueOnce(
				Response.json({
					entries: [{ id: "run-three" }, { id: "run-two", status: "succeeded" }],
					next_cursor: "older-again",
				}),
			);
		const workflow = core.workflows.jobs();

		await workflow.load();
		await workflow.more();
		await workflow.load();

		expect(workflow.runs.data.value).toEqual({
			entries: [
				{ id: "run-three" },
				{ id: "run-two", status: "succeeded" },
				{ id: "run-one" },
			],
			next_cursor: null,
		});
		expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
			"/api/jobs",
			"/api/jobs/runs?limit=20",
			"/api/jobs/runs?limit=20&cursor=older",
			"/api/jobs",
			"/api/jobs/runs?limit=20",
		]);
		workflow.dispose();
	});
});

function discovery(
	positions: number[],
	nextOffset: number | null,
	sourceHasMore = nextOffset !== null,
): Discovery {
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
		source_has_more: sourceHasMore,
		source_url: null,
		stale: false,
		total: 3,
	};
}
