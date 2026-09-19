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
	it("forwards seek positions and playback targets", async () => {
		const body = { position_seconds: 75, expected_playback_id: "123" };
		const backend = await listen(
			createServer(async (request, response) => {
				let raw = "";
				for await (const chunk of request) raw += chunk;
				expect(request.url).toBe("/api/player/seek");
				expect(request.method).toBe("PUT");
				response.writeHead(200, { "content-type": "application/json" });
				response.end(raw);
			}),
		);
		const frontend = await listen(
			createServer(toNodeListener(createApp().use(playerProxy(() => backend)))),
		);
		const response = await fetch(`${frontend}/api/player/seek`, {
			method: "PUT",
			headers: { "content-type": "application/json" },
			body: JSON.stringify(body),
		});
		expect(response.status).toBe(200);
		expect(await response.json()).toEqual(body);
	});

	it("forwards mutation bodies, IDs and conflicts without forwarding browser cookies", async () => {
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
		const app = createApp().use(playerProxy(() => backend));
		const frontend = await listen(createServer(toNodeListener(app)));
		const response = await fetch(`${frontend}/api/queue/clear`, {
			method: "POST",
			headers: {
				origin: frontend,
				"content-type": "application/json",
				"idempotency-key": "test-key",
				cookie: "private=local",
			},
			body: JSON.stringify({ expected_queue_revision: 3 }),
		});
		expect(response.status).toBe(409);
		expect(await response.json()).toEqual({ code: "queue_conflict" });
		expect(forwarded).toEqual({
			origin: backend,
			key: "test-key",
			cookie: undefined,
			body: '{"expected_queue_revision":3}',
		});
	});

	it("keeps the existing local origin boundary and rejects unknown routes", async () => {
		let calls = 0;
		const app = createApp().use(
			playerProxy(() => {
				calls++;
				return "http://127.0.0.1:1";
			}),
		);
		const frontend = await listen(createServer(toNodeListener(app)));
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
		const frontend = await listen(
			createServer(toNodeListener(createApp().use(playerProxy(() => backend)))),
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
		const frontend = await listen(
			createServer(toNodeListener(createApp().use(playerProxy(() => backend)))),
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
