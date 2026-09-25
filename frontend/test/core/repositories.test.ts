// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import { describe, expect, it } from "vitest";
import { fixture } from "./fixture";

describe("new backend repositories", () => {
	it("reads session, profile and history independently, using the new routes", async () => {
		const { client, fetcher } = fixture();
		fetcher.mockImplementation(async () => Response.json({}));
		await client.account.session();
		await client.account.profile("7d");
		await client.listening.recent(10);
		expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
			"/api/auth/session",
			"/api/users/me?period=7d",
			"/api/listening/recent?limit=10",
		]);
		expect(client.account.loginUrl).toBe("/api/auth/discord");
	});

	it("sends profile changes to users/me, not a session or Discord-ID resource", async () => {
		const { client, fetcher } = fixture();
		fetcher.mockImplementation(async () => Response.json({}));
		await client.account.updateProfile({ display_name: "Listener", pixabot: "12ab" });
		expect(fetcher.mock.calls[0]![0]).toBe("/api/users/me/profile");
		expect(fetcher.mock.calls[0]![1]?.method).toBe("PUT");
		expect(JSON.parse(fetcher.mock.calls[0]![1]!.body as string)).toEqual({
			display_name: "Listener",
			pixabot: "12ab",
		});
	});

	it("keeps operation IDs and queue revisions in JSON, including DELETE", async () => {
		const { client, fetcher } = fixture();
		fetcher.mockImplementation(async () => Response.json({}));
		const body = { operation_id: "same-operation", expected_queue_revision: 9 };
		await client.player.remove("entry", body);
		await client.player.remove("entry", body);
		for (const [path, options] of fetcher.mock.calls) {
			expect(path).toBe("/api/player/queue/entry");
			expect(options?.method).toBe("DELETE");
			expect(JSON.parse(options!.body as string)).toEqual(body);
			expect(new Headers(options?.headers).has("Idempotency-Key")).toBe(false);
		}
	});

	it("retains radio generation, precise Discord IDs and the new seek method", async () => {
		const { client, fetcher } = fixture();
		fetcher.mockImplementation(async () => Response.json({}));
		await client.player.retryRadio({
			operation_id: "op-radio",
			expected_generation: "radio-run",
		});
		await client.player.join({ operation_id: "op-voice", channel_id: "1550894913980465212" });
		await client.player.seek({ operation_id: "op-seek", seconds: 42 });
		expect(
			fetcher.mock.calls.map(([url, options]) => [
				url,
				options?.method,
				JSON.parse(options!.body as string),
			]),
		).toEqual([
			[
				"/api/player/radio/retry",
				"POST",
				{ operation_id: "op-radio", expected_generation: "radio-run" },
			],
			[
				"/api/player/voice/join",
				"POST",
				{ operation_id: "op-voice", channel_id: "1550894913980465212" },
			],
			["/api/player/seek", "POST", { operation_id: "op-seek", seconds: 42 }],
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
		await client.catalog.snapshot("search", "snapshot/one", 20, 10);
		await client.statistics.overview("30d");
		await client.statistics.user("user/one", "7d");
		await client.access.state(50);
		await client.access.members();
		await client.access.grant("discord/one");
		await client.access.revoke("discord/one");
		await client.logs.recent(42, 100);

		expect(fetcher.mock.calls.map(([url, options]) => [url, options?.method])).toEqual([
			[
				"/api/catalog/search?q=Zara+Larsson&limit=8&provider=youtube+music&refresh=true",
				undefined,
			],
			[
				"/api/catalog/playlist?url=https%3A%2F%2Fmusic.example%2Fplaylist%3Fid%3D1&provider=youtube+music",
				undefined,
			],
			["/api/catalog/link?url=https%3A%2F%2Fvideo.example%2Fwatch%3Fv%3D1", undefined],
			["/api/catalog/search/snapshot%2Fone?offset=20&limit=10", undefined],
			["/api/statistics/overview?period=30d", undefined],
			["/api/statistics/users/user%2Fone?period=7d", undefined],
			["/api/access?history_limit=50", undefined],
			["/api/access/members", undefined],
			["/api/access/discord%2Fone", "PUT"],
			["/api/access/discord%2Fone", "DELETE"],
			["/api/logs?limit=100&after=42", undefined],
		]);
	});
});
