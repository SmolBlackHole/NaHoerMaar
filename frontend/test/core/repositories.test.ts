// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { describe, expect, it } from "vitest";
import { fixture } from "./fixture";

describe("new backend repositories", () => {
	it("reads session, account, profile and history independently", async () => {
		const { client, fetcher } = fixture();
		fetcher.mockImplementation(async () => Response.json({}));
		await client.account.session();
		await client.account.account();
		await client.account.profile("7d");
		await client.playbacks.list({
			page: 3,
			pageSize: 10,
			query: "Still Alive",
			radio: true,
			requestedBy: "user-one",
			startedFrom: "2026-09-28T10:00:00Z",
			startedTo: "2026-09-28T11:00:00Z",
			endReason: "completed",
			snapshot: "snapshot one",
		});
		expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
			"/api/auth/session",
			"/api/users/me",
			"/api/profiles/me?period=7d",
			"/api/playbacks?page=3&page_size=10&q=Still%20Alive&radio=true&requested_by=user-one&started_from=2026-09-28T10%3A00%3A00Z&started_to=2026-09-28T11%3A00%3A00Z&end_reason=completed&snapshot=snapshot%20one",
		]);
		expect(client.account.loginUrl).toBe("/api/auth/discord");
	});

	it("sends profile changes to users/me, not a session or Discord-ID resource", async () => {
		const { client, fetcher } = fixture();
		fetcher.mockImplementation(async () => Response.json({}));
		await client.account.update({ profile: { display_name: "Listener" } });
		expect(fetcher.mock.calls[0]![0]).toBe("/api/users/me");
		expect(fetcher.mock.calls[0]![1]?.method).toBe("PATCH");
		expect(JSON.parse(fetcher.mock.calls[0]![1]!.body as string)).toEqual({
			profile: { display_name: "Listener" },
		});
	});

	it("keeps operation IDs in headers and queue revisions in JSON", async () => {
		const { client, fetcher } = fixture();
		fetcher.mockImplementation(async () => Response.json({}));
		const body = { expected_queue_revision: 9 };
		await client.player.remove("same-operation", "entry", body);
		await client.player.remove("same-operation", "entry", body);
		for (const [path, options] of fetcher.mock.calls) {
			expect(path).toBe("/api/player/queue/entry");
			expect(options?.method).toBe("DELETE");
			expect(JSON.parse(options!.body as string)).toEqual(body);
			expect(new Headers(options?.headers).get("Idempotency-Key")).toBe("same-operation");
		}
	});

	it("retains radio generation, precise Discord IDs and player control methods", async () => {
		const { client, fetcher } = fixture();
		fetcher.mockImplementation(async () => Response.json({}));
		await client.player.retryRadio("op-radio", {
			expected_generation: "radio-run",
		});
		await client.player.join("op-voice", { channel_id: "1550894913980465212" });
		await client.player.control("op-seek", { action: "seek", seconds: 42 });
		await client.player.setSleepTimer("op-sleep", { seconds: 1800 });
		await client.player.cancelSleepTimer("op-cancel-sleep");
		expect(
			fetcher.mock.calls.map(([url, options]) => [
				url,
				options?.method,
				new Headers(options?.headers).get("Idempotency-Key"),
				options?.body ? JSON.parse(options.body as string) : undefined,
			]),
		).toEqual([
			["/api/player/radio/retry", "POST", "op-radio", { expected_generation: "radio-run" }],
			["/api/player/voice", "PUT", "op-voice", { channel_id: "1550894913980465212" }],
			["/api/player/control", "POST", "op-seek", { action: "seek", seconds: 42 }],
			["/api/player/sleep-timer", "PUT", "op-sleep", { seconds: 1800 }],
			["/api/player/sleep-timer", "DELETE", "op-cancel-sleep", undefined],
		]);
	});

	it("uses the catalog, statistics, access and log contracts", async () => {
		const { client, fetcher } = fixture();
		fetcher.mockImplementation(async () => Response.json({}));
		await client.catalog.search("Zara Larsson", {
			limit: 8,
			provider: "youtube music",
			refresh: true,
		});
		await client.catalog.playlist("https://music.example/playlist?id=1", {
			provider: "youtube music",
		});
		await client.catalog.link("https://video.example/watch?v=1");
		await client.catalog.discovery("snapshot/one", 20, 10);
		await client.catalog.continueDiscovery("snapshot/one", 30, 10);
		await client.statistics.get("30d");
		await client.access.state(50);
		await client.access.members({ query: "Andrey", guildId: "guild/one" });
		await client.access.grant("discord/one");
		await client.access.revoke("discord/one");
		await client.logs.recent({
			after: 42,
			limit: 100,
			query: "queue stalled",
			level: "ERROR",
			source: "player",
			actorId: "user-one",
			requestId: "request-one",
			correlationId: "correlation-one",
			causationId: "causation-one",
		});
		await client.incidents.report({
			period: "7d",
			severity: "error",
			component: "player",
			code: "queue_stalled",
			actorId: "user-one",
			page: 2,
			pageSize: 25,
			snapshot: "incident snapshot",
		});

		expect(fetcher.mock.calls.map(([url, options]) => [url, options?.method])).toEqual([
			[
				"/api/catalog/search?q=Zara%20Larsson&limit=8&provider=youtube%20music&refresh=true",
				"GET",
			],
			[
				"/api/catalog/playlist?url=https%3A%2F%2Fmusic.example%2Fplaylist%3Fid%3D1&provider=youtube%20music",
				"GET",
			],
			["/api/catalog/link?url=https%3A%2F%2Fvideo.example%2Fwatch%3Fv%3D1", "GET"],
			["/api/catalog/discoveries/snapshot%2Fone?offset=20&page_size=10", "GET"],
			[
				"/api/catalog/discoveries/snapshot%2Fone/continuations?offset=30&page_size=10",
				"POST",
			],
			["/api/statistics?period=30d", "GET"],
			["/api/access?history_limit=50", "GET"],
			["/api/access/members?q=Andrey&guild_id=guild%2Fone", "GET"],
			["/api/access/discord%2Fone", "PUT"],
			["/api/access/discord%2Fone", "DELETE"],
			[
				"/api/logs?after=42&limit=100&q=queue%20stalled&level=ERROR&source=player&actor_id=user-one&request_id=request-one&correlation_id=correlation-one&causation_id=causation-one",
				"GET",
			],
			[
				"/api/incidents?period=7d&severity=error&component=player&code=queue_stalled&actor_id=user-one&page=2&page_size=25&snapshot=incident%20snapshot",
				"GET",
			],
		]);
	});

	it("reads and triggers catalog maintenance through the jobs repository", async () => {
		const { client, fetcher } = fixture();
		fetcher.mockImplementation(async () => Response.json({ jobs: [] }));

		await client.jobs.status();
		await client.jobs.runs({
			pageSize: 10,
			cursor: "next page",
			jobId: "housekeeping",
			status: "failed",
		});
		await client.jobs.run("run/id");
		await client.jobs.runJob("catalog-maintenance", { batch_size: 25 });
		await client.jobs.runJob("housekeeping", { batch_size: 500 });
		await client.jobs.runJob("catalog-cleanup", {
			batch_size: 100,
			preview: true,
			age_days: 90,
		});

		expect(fetcher.mock.calls.map(([url, options]) => [url, options?.method])).toEqual([
			["/api/jobs", "GET"],
			[
				"/api/jobs/runs?page_size=10&cursor=next%20page&job_id=housekeeping&status=failed",
				"GET",
			],
			["/api/jobs/runs/run%2Fid", "GET"],
			["/api/jobs/catalog-maintenance/runs", "POST"],
			["/api/jobs/housekeeping/runs", "POST"],
			["/api/jobs/catalog-cleanup/runs", "POST"],
		]);
		expect(JSON.parse(fetcher.mock.calls[3]![1]!.body as string)).toEqual({
			batch_size: 25,
		});
		expect(JSON.parse(fetcher.mock.calls[4]![1]!.body as string)).toEqual({
			batch_size: 500,
		});
		expect(JSON.parse(fetcher.mock.calls[5]![1]!.body as string)).toEqual({
			batch_size: 100,
			preview: true,
			age_days: 90,
		});
	});
});
