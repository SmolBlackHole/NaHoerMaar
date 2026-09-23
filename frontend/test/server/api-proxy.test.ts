import { createServer, request as httpRequest, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, eventHandler, toNodeListener } from "h3";
import apiProxy from "../../server/api/[...path]";
import canonicalOrigin from "../../server/middleware/canonical-origin";

const servers: Server[] = [];
let backendUrl = "";
let publicOrigin = "http://localhost:3000";

async function listen(server: Server) {
	servers.push(server);
	await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
	return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
}

function request(
	url: string,
	options: { method?: string; headers?: Record<string, string>; body?: string } = {},
) {
	return new Promise<{
		status?: number;
		headers: import("node:http").IncomingHttpHeaders;
		body: string;
	}>((resolve, reject) => {
		const outgoing = httpRequest(
			url,
			{ method: options.method, headers: options.headers },
			(response) => {
				let body = "";
				response.setEncoding("utf8");
				response.on("data", (chunk) => (body += chunk));
				response.on("end", () =>
					resolve({ status: response.statusCode, headers: response.headers, body }),
				);
			},
		);
		outgoing.on("error", reject);
		if (options.body) outgoing.write(options.body);
		outgoing.end();
	});
}

beforeEach(() => {
	vi.stubGlobal("useRuntimeConfig", () => ({ backendUrl, publicOrigin }));
});

afterEach(async () => {
	vi.unstubAllGlobals();
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

describe("Nitro API proxy", () => {
	it("passes the complete trusted request and backend response through", async () => {
		let received: Record<string, unknown> = {};
		backendUrl = await listen(
			createServer(async (incoming, response) => {
				let body = "";
				for await (const chunk of incoming) body += chunk;
				received = {
					method: incoming.method,
					url: incoming.url,
					origin: incoming.headers.origin,
					cookie: incoming.headers.cookie,
					body,
				};
				response.writeHead(409, {
					"content-type": "application/json",
					"set-cookie": [
						"nahormaar_session=new; Path=/api; HttpOnly; SameSite=Lax",
						"nahormaar_login=; Max-Age=0; Path=/api/auth",
					],
				});
				response.end('{"code":"queue_conflict"}');
			}),
		);
		const frontend = await listen(createServer(toNodeListener(createApp().use(apiProxy))));
		const result = await request(`${frontend}/api/queue?keep=all`, {
			method: "POST",
			headers: {
				origin: "http://localhost:3000",
				cookie: "nahormaar_session=session; consent=accepted",
				"content-type": "application/json",
			},
			body: '{"track_ids":["one"]}',
		});

		expect(result.status).toBe(409);
		expect(result.headers["set-cookie"]).toHaveLength(2);
		expect(JSON.parse(result.body)).toEqual({ code: "queue_conflict" });
		expect(received).toEqual({
			method: "POST",
			url: "/api/queue?keep=all",
			origin: "http://localhost:3000",
			cookie: "nahormaar_session=session; consent=accepted",
			body: '{"track_ids":["one"]}',
		});
	});

	it("passes redirects and unknown API routes through unchanged", async () => {
		backendUrl = await listen(
			createServer((incoming, response) => {
				if (incoming.url === "/api/auth/discord") {
					response.writeHead(303, { location: "https://discord.com/oauth2/authorize" });
					response.end();
					return;
				}
				response.writeHead(404, { "content-type": "application/json" });
				response.end('{"detail":"Not Found"}');
			}),
		);
		const frontend = await listen(createServer(toNodeListener(createApp().use(apiProxy))));

		const redirect = await request(`${frontend}/api/auth/discord`);
		expect(redirect.status).toBe(303);
		expect(redirect.headers.location).toBe("https://discord.com/oauth2/authorize");
		const missing = await request(`${frontend}/api/future-endpoint`);
		expect(missing.status).toBe(404);
		expect(JSON.parse(missing.body)).toEqual({ detail: "Not Found" });
	});

	it("streams events before the backend response finishes", async () => {
		let disconnected!: () => void;
		const closed = new Promise<void>((resolve) => {
			disconnected = resolve;
		});
		backendUrl = await listen(
			createServer((_incoming, response) => {
				response.writeHead(200, { "content-type": "text/event-stream" });
				response.write('event: state\ndata: {"revision":1}\n\n');
				response.once("close", disconnected);
			}),
		);
		const frontend = await listen(createServer(toNodeListener(createApp().use(apiProxy))));
		const abort = new AbortController();
		const response = await fetch(`${frontend}/api/events`, { signal: abort.signal });
		const first = await response.body!.getReader().read();
		expect(new TextDecoder().decode(first.value)).toContain('"revision":1');
		abort.abort();
		await expect(
			Promise.race([
				closed.then(() => "closed"),
				new Promise<string>((resolve) => setTimeout(() => resolve("open"), 1500)),
			]),
		).resolves.toBe("closed");
	});

	it("redirects local browser navigation to the configured loopback origin", async () => {
		const app = createApp()
			.use(canonicalOrigin)
			.use(eventHandler(() => "ok"));
		const frontend = await listen(createServer(toNodeListener(app)));
		const port = new URL(frontend).port;
		publicOrigin = `http://localhost:${port}`;

		const redirected = await request(`${frontend}/queue?view=radio`, {
			headers: { accept: "text/html", host: `127.0.0.1:${port}` },
		});
		expect(redirected.status).toBe(307);
		expect(redirected.headers.location).toBe(`http://localhost:${port}/queue?view=radio`);

		const health = await request(`${frontend}/`, {
			headers: { accept: "*/*", host: `127.0.0.1:${port}` },
		});
		expect(health.status).toBe(200);
		expect(health.body).toBe("ok");
	});
});
