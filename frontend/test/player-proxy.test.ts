import { createServer, request as httpRequest, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import { createApp, toNodeListener } from "h3";
import { playerProxy } from "../server/utils/playerProxy";

const servers: Server[] = [];
async function listen(server: Server) {
	servers.push(server);
	await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
	return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
}
afterEach(async () => {
	await Promise.all(
		servers.splice(0).map(
			(server) =>
				new Promise<void>((resolve) => {
					server.closeAllConnections();
					server.close(() => resolve());
				}),
		),
	);
});

describe("local API proxy", () => {
	it("preserves callback redirects and both cookies behind the configured HTTPS origin", async () => {
		const calls: string[] = [];
		const backend = await listen(
			createServer((request, response) => {
				calls.push(request.url!);
				expect(request.headers["x-forwarded-host"]).toBeUndefined();
				response.writeHead(303, {
					location: "/",
					"set-cookie": [
						"nahormaar_session=new; Path=/api; Secure; HttpOnly; SameSite=Lax",
						"nahormaar_login=; Max-Age=0; Path=/api/auth",
					],
					"cache-control": "no-store",
				});
				response.end();
			}),
		);
		const frontend = await listen(
			createServer(
				toNodeListener(
					createApp().use(
						playerProxy(
							() => backend,
							() => "https://music.example.test",
						),
					),
				),
			),
		);
		const result = await new Promise<{
			status?: number;
			location?: string;
			cookies?: string[];
		}>((resolve, reject) => {
			const request = httpRequest(
				`${frontend}/api/auth/discord/callback?state=bound&code=one-use&ignored=1`,
				{
					headers: {
						host: "music.example.test",
						"sec-fetch-site": "cross-site",
						"x-forwarded-host": "untrusted.invalid",
					},
				},
				(response) => {
					response.resume();
					resolve({
						status: response.statusCode,
						location: response.headers.location,
						cookies: response.headers["set-cookie"],
					});
				},
			);
			request.on("error", reject);
			request.end();
		});
		expect(result.status).toBe(303);
		expect(result.location).toBe("/");
		expect(result.cookies).toHaveLength(2);
		expect(result.cookies?.[0]).toContain("Secure; HttpOnly; SameSite=Lax");
		expect(calls).toEqual(["/api/auth/discord/callback?state=bound&code=one-use"]);
	});
	it("forwards discovery queries, preview lifecycle and atomic imports", async () => {
		const calls: {
			url: string | undefined;
			method: string | undefined;
			key: string | string[] | undefined;
			body: string;
		}[] = [];
		const backend = await listen(
			createServer(async (request, response) => {
				let body = "";
				for await (const chunk of request) body += chunk;
				calls.push({
					url: request.url,
					method: request.method,
					key: request.headers["idempotency-key"],
					body,
				});
				response.writeHead(200, { "content-type": "application/json" });
				response.end("{}");
			}),
		);
		const frontend: string = await listen(
			createServer(
				toNodeListener(
					createApp().use(
						playerProxy(
							() => backend,
							() => frontend,
						),
					),
				),
			),
		);
		const id = "c68fe9f1-ac72-4f15-9e7f-445d332b9ca7";
		for (const [path, method] of [
			[
				"catalog/search?q=" +
					encodeURIComponent("Амура & remix") +
					"&offset=10&source=youtube_music&snapshot_id=pinned-version&ignored=1",
				"GET",
			],
			["youtube/playlists", "POST"],
			[`youtube/playlists/${id}`, "GET"],
			[`youtube/playlists/${id}`, "DELETE"],
			["queue/batch", "POST"],
		]) {
			const response = await fetch(`${frontend}/api/${path}`, {
				method,
				headers: { "idempotency-key": id, "content-type": "application/json" },
				body: method === "POST" ? '{"source_urls":["example"]}' : undefined,
			});
			expect(response.status).toBe(200);
		}
		expect(new URL(calls[0]!.url!, backend).searchParams.get("q")).toBe("Амура & remix");
		expect(new URL(calls[0]!.url!, backend).searchParams.get("offset")).toBe("10");
		expect(new URL(calls[0]!.url!, backend).searchParams.get("source")).toBe("youtube_music");
		expect(new URL(calls[0]!.url!, backend).searchParams.get("snapshot_id")).toBe(
			"pinned-version",
		);
		expect(calls[0]!.url).not.toContain("ignored");
		expect(calls.map((call) => call.method)).toEqual(["GET", "POST", "GET", "DELETE", "POST"]);
		expect(calls[4]).toEqual({
			url: "/api/queue/batch",
			method: "POST",
			key: id,
			body: '{"source_urls":["example"]}',
		});
	});

	it.each([
		["player/seek", { position_seconds: 75, expected_playback_id: "123" }],
		["profile/appearance", { mode: "light", primaryColor: "amber" }],
	])("forwards PUT /api/%s", async (path, body) => {
		const backend = await listen(
			createServer(async (request, response) => {
				let raw = "";
				for await (const chunk of request) raw += chunk;
				expect(request.url).toBe(`/api/${path}`);
				expect(request.method).toBe("PUT");
				response.writeHead(200, { "content-type": "application/json" });
				response.end(raw);
			}),
		);
		const frontend: string = await listen(
			createServer(
				toNodeListener(
					createApp().use(
						playerProxy(
							() => backend,
							() => frontend,
						),
					),
				),
			),
		);
		const response = await fetch(`${frontend}/api/${path}`, {
			method: "PUT",
			headers: { "content-type": "application/json" },
			body: JSON.stringify(body),
		});
		expect(response.status).toBe(200);
		expect(await response.json()).toEqual(body);
	});

	it("forwards mutation bodies, IDs and conflicts while forwarding only session cookies", async () => {
		let forwarded: { origin?: string; key?: string; cookie?: string; body: string } | undefined;
		const backend = await listen(
			createServer(async (request, response) => {
				let body = "";
				for await (const chunk of request) body += chunk;
				forwarded = {
					origin: request.headers.origin,
					key: request.headers["idempotency-key"] as string,
					cookie: request.headers.cookie,
					body,
				};
				response.writeHead(409, { "content-type": "application/json" });
				response.end(JSON.stringify({ code: "queue_conflict" }));
			}),
		);
		const app = createApp().use(
			playerProxy(
				() => backend,
				() => frontend,
			),
		);
		const frontend: string = await listen(createServer(toNodeListener(app)));
		const response = await fetch(`${frontend}/api/queue/clear`, {
			method: "POST",
			headers: {
				origin: frontend,
				"content-type": "application/json",
				"idempotency-key": "test-key",
				cookie: "private=local; nahormaar_session=session-token",
			},
			body: JSON.stringify({ expected_queue_revision: 3 }),
		});
		expect(response.status).toBe(409);
		expect(await response.json()).toEqual({ code: "queue_conflict" });
		expect(forwarded).toEqual({
			origin: frontend,
			key: "test-key",
			cookie: "nahormaar_session=session-token",
			body: '{"expected_queue_revision":3}',
		});
	});

	it("keeps the existing local origin boundary and rejects unknown routes", async () => {
		let calls = 0;
		const app = createApp().use(
			playerProxy(
				() => {
					calls++;
					return "http://127.0.0.1:1";
				},
				() => frontend,
			),
		);
		const frontend: string = await listen(createServer(toNodeListener(app)));
		for (const headers of [
			{ origin: "https://example.org" },
			{ "sec-fetch-site": "cross-site" },
			{ host: "example.org" },
		]) {
			const status = await new Promise<number | undefined>((resolve, reject) => {
				const request = httpRequest(`${frontend}/api/state`, { headers }, (response) => {
					response.resume();
					resolve(response.statusCode);
				});
				request.on("error", reject);
				request.end();
			});
			expect(status, JSON.stringify(headers)).toBe(403);
		}
		expect((await fetch(`${frontend}/api/unrelated`)).status).toBe(404);
		expect(calls).toBe(0);
	});

	it("streams updates before the backend finishes and closes upstream when the browser leaves", async () => {
		let disconnected!: () => void;
		const closed = new Promise<void>((resolve) => {
			disconnected = resolve;
		});
		const backend = await listen(
			createServer((request, response) => {
				response.writeHead(200, {
					"content-type": "text/event-stream",
					"cache-control": "no-cache",
				});
				response.write('event: state\ndata: {"revision":1}\n\n');
				response.once("close", disconnected);
			}),
		);
		const frontend: string = await listen(
			createServer(
				toNodeListener(
					createApp().use(
						playerProxy(
							() => backend,
							() => frontend,
						),
					),
				),
			),
		);
		const abort = new AbortController();
		const response = await fetch(`${frontend}/api/events`, { signal: abort.signal });
		const reader = response.body!.getReader();
		const chunk = await reader.read();
		expect(new TextDecoder().decode(chunk.value)).toContain('"revision":1');
		abort.abort();
		await closed;
	});

	it("closes an active event stream when the backend connection breaks", async () => {
		let disconnectBackend!: () => void;
		const backend = await listen(
			createServer((_request, response) => {
				response.writeHead(200, { "content-type": "text/event-stream" });
				response.write('event: state\ndata: {"revision":1}\n\n');
				disconnectBackend = () => response.destroy();
			}),
		);
		const frontend: string = await listen(
			createServer(
				toNodeListener(
					createApp().use(
						playerProxy(
							() => backend,
							() => frontend,
						),
					),
				),
			),
		);
		const abort = new AbortController();
		const response = await fetch(`${frontend}/api/events`, { signal: abort.signal });
		const reader = response.body!.getReader();
		await reader.read();
		disconnectBackend();
		let timer: ReturnType<typeof setTimeout> | undefined;
		try {
			const outcome = await Promise.race([
				reader.read().then(
					(result) => (result.done ? "ended" : "still open"),
					() => "disconnected",
				),
				new Promise<string>((resolve) => {
					timer = setTimeout(() => resolve("still open"), 1500);
				}),
			]);
			expect(outcome).not.toBe("still open");
		} finally {
			clearTimeout(timer);
			abort.abort();
		}
	});
});
