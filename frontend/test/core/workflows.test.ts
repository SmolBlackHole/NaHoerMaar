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
		const history = core.workflows.playbackHistory();
		const pending = history.load();
		const signal = fetcher.mock.calls[0]![1]?.signal;
		expect(fetcher.mock.calls[0]![0]).toBe("/api/playbacks?page=1&page_size=20");

		core.authority.lost("signed_out");
		expect(signal?.aborted).toBe(true);
		finishRead(
			Response.json({
				items: [],
				page: 1,
				page_size: 20,
				total: 0,
				page_count: 0,
				snapshot: null,
			}),
		);
		expect(await pending).toBeNull();
		expect(history.history.data.value).toBeNull();
		expect(history.history.loading.value).toBe(false);
		history.dispose();
	});

	it("keeps numbered history pages on one stable snapshot", async () => {
		const fetcher = vi.fn<typeof fetch>();
		const core = createBackendCore({ fetch: fetcher });
		core.authority.replace("session-token", "restored");
		fetcher
			.mockResolvedValueOnce(
				Response.json({
					items: [{ playback_id: "first" }],
					page: 1,
					page_size: 1,
					total: 2,
					page_count: 2,
					snapshot: "snapshot-one",
				}),
			)
			.mockResolvedValueOnce(
				Response.json({
					items: [{ playback_id: "second" }],
					page: 2,
					page_size: 1,
					total: 2,
					page_count: 2,
					snapshot: "snapshot-one",
				}),
			)
			.mockResolvedValueOnce(
				Response.json({
					items: [{ playback_id: "radio" }],
					contributors: [],
					page: 1,
					page_size: 1,
					total: 1,
					page_count: 1,
					snapshot: "snapshot-two",
				}),
			);
		const workflow = core.workflows.playbackHistory();

		await workflow.load({ pageSize: 1, query: "Still Alive", newSnapshot: true });
		await workflow.load({ page: 2, pageSize: 1, query: "Still Alive" });
		await workflow.load({
			pageSize: 1,
			query: "Still Alive",
			filters: {
				radio: true,
				requestedBy: "user-one",
				startedFrom: "2026-09-28T10:00:00Z",
				startedTo: "2026-09-28T11:00:00Z",
				endReason: "completed",
			},
		});

		expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
			"/api/playbacks?page=1&page_size=1&q=Still%20Alive",
			"/api/playbacks?page=2&page_size=1&q=Still%20Alive&snapshot=snapshot-one",
			"/api/playbacks?page=1&page_size=1&q=Still%20Alive&radio=true&requested_by=user-one&started_from=2026-09-28T10%3A00%3A00Z&started_to=2026-09-28T11%3A00%3A00Z&end_reason=completed",
		]);
		expect(workflow.history.data.value?.items.map(({ playback_id }) => playback_id)).toEqual([
			"radio",
		]);
		expect(workflow.history.data.value?.page).toBe(1);
		workflow.dispose();
	});

	it("pages a filtered incident report without replacing full-period totals", async () => {
		const fetcher = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(
				Response.json({
					items: [{ id: "incident-one" }],
					page: 1,
					page_size: 1,
					total: 2,
					page_count: 2,
					snapshot: "incident-snapshot",
					totals: { errors: 2 },
				}),
			)
			.mockResolvedValueOnce(
				Response.json({
					items: [{ id: "incident-two" }],
					page: 2,
					page_size: 1,
					total: 2,
					page_count: 2,
					snapshot: "incident-snapshot",
					totals: { errors: 2 },
				}),
			);
		const core = createBackendCore({ fetch: fetcher });
		core.authority.replace("session-token", "restored");
		const workflow = core.workflows.incidents();
		const filters = {
			period: "7d" as const,
			severity: "error" as const,
			component: "player",
			code: "queue_stalled",
			actorId: "user-one",
		};

		await workflow.load({ pageSize: 1, filters });
		await workflow.load({ page: 2, pageSize: 1, filters });

		expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
			"/api/incidents?period=7d&severity=error&component=player&code=queue_stalled&actor_id=user-one&page=1&page_size=1",
			"/api/incidents?period=7d&severity=error&component=player&code=queue_stalled&actor_id=user-one&page=2&page_size=1&snapshot=incident-snapshot",
		]);
		expect(workflow.report.data.value?.items).toEqual([{ id: "incident-two" }]);
		expect(workflow.report.data.value?.totals.errors).toBe(2);
		workflow.dispose();
	});

	it("applies log filters before polling from the current cursor", async () => {
		const fetcher = vi
			.fn<typeof fetch>()
			.mockResolvedValueOnce(Response.json({ items: [{ id: 10 }], cursor: 10 }))
			.mockResolvedValueOnce(Response.json({ items: [{ id: 11 }], cursor: 11 }));
		const core = createBackendCore({ fetch: fetcher });
		core.authority.replace("session-token", "restored");
		const workflow = core.workflows.logs();
		const filters = { query: "queue", level: "ERROR", source: "player" };

		await workflow.loadLatest(filters, 5);
		await workflow.poll(filters, 5);

		expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
			"/api/logs?limit=5&q=queue&level=ERROR&source=player",
			"/api/logs?after=10&limit=5&q=queue&level=ERROR&source=player",
		]);
		expect(workflow.page.data.value?.items).toEqual([{ id: 10 }, { id: 11 }]);
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
			"/api/catalog/discoveries/version-id?offset=1&page_size=20",
		]);
		expect(workflow.results.data.value?.items.map(({ position }) => position)).toEqual([
			0, 1, 2,
		]);
		expect(workflow.results.data.value?.items[1]?.track.title).toBe("page-two-1");
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
			["/api/catalog/search?q=ambient", "GET"],
			["/api/catalog/discoveries/version-id/continuations?offset=2&page_size=20", "POST"],
		]);
		expect(workflow.results.data.value?.version).toBe("continued-version");
		expect(workflow.results.data.value?.items.map(({ position }) => position)).toEqual([
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
			.mockResolvedValueOnce(Response.json({ items: [], page_size: 20, next_cursor: null }))
			.mockResolvedValueOnce(
				Response.json({
					...idle,
					running: true,
					active_options: { batch_size: 25, preview: null, age_days: null },
				}),
			);
		const workflow = core.workflows.jobs();

		await workflow.load();
		await workflow.runJob("catalog-maintenance", { batch_size: 25 });

		expect(workflow.jobs.data.value?.jobs[0]).toMatchObject({
			id: "catalog-maintenance",
			running: true,
			active_options: { batch_size: 25 },
		});
		expect(fetcher.mock.calls.map(([url, options]) => [url, options?.method])).toEqual([
			["/api/jobs", "GET"],
			["/api/jobs/runs?page_size=20", "GET"],
			["/api/jobs/catalog-maintenance/runs", "POST"],
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
			.mockResolvedValueOnce(
				Response.json({ items: [runSummary], page_size: 20, next_cursor: null }),
			)
			.mockResolvedValueOnce(
				Response.json({
					...idle,
					running: true,
					active_options: { batch_size: 500, preview: null, age_days: null },
				}),
			)
			.mockResolvedValueOnce(Response.json(recentRun));
		const workflow = core.workflows.jobs();

		await workflow.load();
		await workflow.runJob("housekeeping", { batch_size: 500 });
		await workflow.loadRun("run-one");
		await workflow.loadRun("run-one");

		expect(workflow.jobs.data.value?.jobs[0]).toMatchObject({
			id: "housekeeping",
			running: true,
			active_options: { batch_size: 500 },
		});
		expect(workflow.runs.data.value?.items).toEqual([runSummary]);
		expect(workflow.runDetail("run-one").data.value?.details).toEqual(recentRun.details);
		expect(fetcher.mock.calls.map(([url, options]) => [url, options?.method])).toEqual([
			["/api/jobs", "GET"],
			["/api/jobs/runs?page_size=20", "GET"],
			["/api/jobs/housekeeping/runs", "POST"],
			["/api/jobs/runs/run-one", "GET"],
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
				Response.json({ items: [{ id: "run-two" }], page_size: 20, next_cursor: "older" }),
			)
			.mockResolvedValueOnce(
				Response.json({ items: [{ id: "run-one" }], page_size: 20, next_cursor: null }),
			)
			.mockResolvedValueOnce(Response.json(status))
			.mockResolvedValueOnce(
				Response.json({
					items: [{ id: "run-three" }, { id: "run-two", status: "succeeded" }],
					page_size: 20,
					next_cursor: "older-again",
				}),
			);
		const workflow = core.workflows.jobs();

		await workflow.load();
		await workflow.more();
		await workflow.load();

		expect(workflow.runs.data.value).toEqual({
			items: [{ id: "run-three" }, { id: "run-two", status: "succeeded" }, { id: "run-one" }],
			next_cursor: null,
			page_size: 20,
		});
		expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
			"/api/jobs",
			"/api/jobs/runs?page_size=20",
			"/api/jobs/runs?page_size=20&cursor=older",
			"/api/jobs",
			"/api/jobs/runs?page_size=20",
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
		items: positions.map((position) => ({
			position,
			source: {} as Discovery["items"][number]["source"],
			track: {
				id: `track-${position}`,
				title: `page-${nextOffset === null ? "two" : "one"}-${position}`,
			} as Discovery["items"][number]["track"],
		})),
		expires_at: "2026-09-26T00:00:00Z",
		fetched_at: "2026-09-25T00:00:00Z",
		next_offset: nextOffset,
		offset: nextOffset === null ? 1 : 0,
		page_size: positions.length,
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
